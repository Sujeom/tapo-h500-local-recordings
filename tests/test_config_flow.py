"""Config-flow shape, checked statically.

Home Assistant is not installed, so the flow cannot be executed here. What can
be checked without it is the part that actually breaks in front of a user: a
field offered by a form but missing from en.json renders as a raw key like
"poll_interval", and a setting written to the wrong place is stored, ignored,
and silently replaced by its default.
"""
import ast
import importlib
import json
import re
import socket
import sys
import types
import unittest
from pathlib import Path

try:
    import requests
except ImportError:                                   # pragma: no cover
    # Guarded the way pytapo is, a few lines down, and for the same reason:
    # this file must pass on its own wherever it runs. requests is NOT stubbed
    # when absent -- the shapes below assert that its exceptions really are
    # OSError subclasses, which is the premise the auth classifier rests on,
    # and a stand-in would answer that question by construction.
    requests = None

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
SOURCE = (COMPONENT / "config_flow.py").read_text()
SETUP = ast.parse((COMPONENT / "__init__.py").read_text())
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text())
# Both files, because Home Assistant reads en.json and the store reads
# strings.json: a key added to one and forgotten in the other is a form that
# renders raw keys for everybody who never installed from HACS.
LABEL_FILES = {
    name: json.loads((COMPONENT / name).read_text())
    for name in ("strings.json", "translations/en.json")
}

package = types.ModuleType("tapo_h500")
package.__path__ = [str(COMPONENT)]
sys.modules.setdefault("tapo_h500", package)
const = importlib.import_module("tapo_h500.const")

try:
    import pytapo  # noqa: F401
except ImportError:
    # api.py imports pytapo at module level and this suite runs on a Python
    # that does not have it. The classifier touches none of it, so the same
    # hollow stand-ins tests/test_api.py installs are enough -- kept here so
    # this file passes on its own whatever order discovery uses.
    _session = types.ModuleType("pytapo.media_stream.session")
    _session.HttpMediaSession = type("HttpMediaSession", (), {})
    _crypto = types.ModuleType("pytapo.media_stream.crypto")
    _crypto.AESHelper = type("AESHelper", (), {})
    _stream = types.ModuleType("pytapo.media_stream")
    _stream.session, _stream.crypto = _session, _crypto
    _pytapo = types.ModuleType("pytapo")
    _pytapo.Tapo = type("Tapo", (), {})
    sys.modules.update({
        "pytapo": _pytapo, "pytapo.media_stream": _stream,
        "pytapo.media_stream.session": _session,
        "pytapo.media_stream.crypto": _crypto,
    })
api = importlib.import_module("tapo_h500.api")

# The few keys that come from homeassistant.const rather than the component.
HA_KEYS = {
    "CONF_HOST": "host",
    "CONF_USERNAME": "username",
    "CONF_PASSWORD": "password",
}


def _schema_fields(after: str) -> set[str]:
    """Constant names used in the first vol.Schema following a marker."""
    body = SOURCE.split(after, 1)[1].split("return self.async_show_form", 1)[0]
    # Digits matter: CONF_CONVERT_MP4 truncates to CONF_CONVERT_MP without them.
    names = re.findall(r"vol\.Required\(\s*(CONF_[A-Z0-9_]+)", body)
    keys = set()
    for name in names:
        keys.add(HA_KEYS.get(name) or getattr(const, name))
    return keys


class SetupForm(unittest.TestCase):
    # The form offering the interval, both forms validating it with the same
    # object, and both starting at the same default are driven in
    # test_options_flow.OneBoundForBothForms; where the value is stored is
    # driven in test_small_gaps.TheOneSetupLogin. Counting how many times a
    # regex matched said there was one bound, not that both forms used it.

    def test_the_default_is_inside_the_bound(self):
        """The one claim a run cannot make: voluptuous is stubbed, so no test
        that submits a value can find out what the real bound rejects. This
        compares the two constants directly."""
        low, high = re.search(
            r"vol\.Range\(min=(\d+), max=(\d+)\)", SOURCE).groups()
        self.assertGreaterEqual(const.DEFAULT_POLL_INTERVAL, int(low))
        self.assertLessEqual(const.DEFAULT_POLL_INTERVAL, int(high))


class Labels(unittest.TestCase):
    def test_every_setup_field_has_a_label(self):
        """An unlabelled field renders as its raw key in the UI."""
        labelled = set(STRINGS["config"]["step"]["user"]["data"])
        self.assertEqual(_schema_fields("async_step_user") - labelled, set())

    def test_every_options_field_has_a_label(self):
        # init is now a menu; the settings form moved to its own step.
        labelled = set(STRINGS["options"]["step"]["settings"]["data"])
        self.assertEqual(_schema_fields("async_step_settings") - labelled, set())


class FaceNaming(unittest.TestCase):
    """Names are set from the integration's own screen, not from a card."""

    def test_the_options_menu_offers_naming(self):
        menu = STRINGS["options"]["step"]["init"]["menu_options"]
        self.assertIn("faces", menu)
        self.assertIn("settings", menu)

    def test_saving_settings_cannot_wipe_the_names(self):
        """Options are replaced wholesale on save. The settings form does not
        ask about face names, so without merging, saving any option at all
        silently deleted every name."""
        self.assertIn("def _merged", SOURCE)
        self.assertRegex(SOURCE, r"return \{\*\*self\.config_entry\.options, \*\*user_input\}")
        self.assertIn("self.async_create_entry(data=self._merged(user_input))", SOURCE)

    def test_saving_names_cannot_wipe_the_settings(self):
        """The same hazard in the other direction."""
        self.assertIn("data={**self.config_entry.options, CONF_FACE_NAMES: names}",
                      SOURCE)

    def test_already_named_faces_stay_editable(self):
        """Otherwise a name could be added but never corrected once that
        person stopped appearing in the window."""
        self.assertIn("set(seen) | set(names)", SOURCE)

    def test_clearing_a_box_removes_the_name(self):
        self.assertIn("names.pop(str(face_id), None)", SOURCE)

    def test_no_faces_yet_is_explained(self):
        self.assertIn('self.async_abort(reason="no_faces")', SOURCE)
        self.assertIn("no_faces", STRINGS["options"]["abort"])

    def test_a_photo_is_linked_so_the_number_can_be_matched_to_a_person(self):
        """The whole point: nobody can name 123456789012 from memory."""
        self.assertIn("def _photo_url", SOURCE)
        self.assertIn("see photo", SOURCE)
        self.assertIn("signed_url(self.hass, path)", SOURCE)

    def test_the_link_is_absolute_so_it_actually_resolves(self):
        """signed_url returns a root-relative path. A card puts that in an
        <img src> and the browser resolves it, but a markdown link is handled
        by the frontend router, which treats /media/local/... as an in-app
        route, finds no such page and goes nowhere. That is why the first
        version of this link did nothing when clicked.
        """
        body = SOURCE.split("def _photo_url", 1)[1]
        self.assertIn("get_url(self.hass)", body)
        self.assertIn('rstrip(\'/\')', body)

    def test_an_installation_with_no_configured_url_still_gets_something(self):
        """The relative form is still correct for anything resolving it
        against the origin, so offer it rather than nothing."""
        body = SOURCE.split("def _photo_url", 1)[1]
        self.assertIn("except NoURLAvailableError:", body)
        self.assertIn("return signed", body)

    def test_no_link_is_offered_before_the_clip_has_downloaded(self):
        """The thumbnail is written by the download, so linking
        unconditionally would offer a dead link for anyone seen this minute."""
        body = SOURCE.split("def _photo_url", 1)[1].split("async def", 1)[0]
        self.assertIn("if not path.is_file():", body)
        self.assertIn("return None", body)

    def test_the_disk_check_does_not_block_the_event_loop(self):
        """is_file() on every face is filesystem work inside a callback."""
        self.assertIn("async_add_executor_job(\n            self._face_lines",
                      SOURCE)

    def test_the_form_says_what_each_number_is(self):
        """A column of raw ids with text boxes tells nobody anything."""
        self.assertIn("description_placeholders", SOURCE)
        self.assertIn("{faces}", STRINGS["options"]["step"]["faces"]["description"])


class AuthClassifier(unittest.TestCase):
    """Which failures may stop the retries, and which may never.

    This hub wedges under repeated authentication and recovers only on a
    timeout, so the cost of guessing wrong is asymmetric: a retry too many is
    a slow setup, while calling a wedge an auth failure abandons the entry and
    tells its owner to retype a password that was never wrong.
    """

    def test_no_wedge_shape_is_ever_called_an_auth_failure(self):
        shapes = [
            OSError("No route to host"),
            ConnectionResetError("Connection reset by peer"),
            TimeoutError("timed out"),
            socket.timeout("timed out"),
            *([] if requests is None else [
                requests.exceptions.ConnectionError("Connection refused"),
                requests.exceptions.ReadTimeout("Read timed out"),
                # A hub answering HTML, or nothing at all, to a JSON request.
                requests.exceptions.JSONDecodeError("Expecting value", "", 0),
            ]),
            ValueError("Expecting value: line 1 column 1 (char 0)"),
            # pytapo raises this verbatim on the -40413 nonce path after its
            # own retries, so the string says nothing about credentials.
            Exception("Invalid authentication data"),
            # The lockout. Waiting it out is the fix; a new password is not.
            Exception("Temporary Suspension: Try again in 300 seconds"),
        ]
        for err in shapes:
            with self.subTest(shape=type(err).__name__ + ": " + str(err)):
                self.assertFalse(api.is_auth_failure(err))
        # The dropped idea: check_media_port talks to port 8800, a different
        # service from the one connect() logs into, and answers a wedge with
        # "wedged" -- so a probe-based rule would have failed the wrong way
        # on exactly the shapes above.
        source = (COMPONENT / "api.py").read_text()
        classifier = source.split(
            "def is_auth_failure", 1)[1].split("\nclass ", 1)[0]
        # _refused_code does the code extraction, so the ban has to cover it
        # too or the probe idea could simply move one function up.
        classifier += source.split(
            "def _refused_code", 1)[1].split("\nclass ", 1)[0]
        for banned in ("check_media", "8800", "Invalid authentication data"):
            self.assertNotIn(banned, classifier)

    def test_a_rejection_carrying_an_error_code_is_an_auth_failure(self):
        """The hub's own refusal, in the only shape it reaches a caller in:
        pytapo's "Error: <msg>, Response: {...}" text.

        -40414 is NEED_LOGIN_BY_LOCAL_PASSWORD, which means the credentials
        and nothing else. Not -40209: pytapo's table calls that one "Invalid
        login credentials", but that table is generic to every Tapo device and
        this hub answers -40209 for a method called with the wrong shape.
        """
        self.assertTrue(api.is_auth_failure(Exception(
            'Error: Need login by local password, '
            'Response: {"error_code": -40414}')))

    def test_a_wrong_shape_refusal_is_not_a_wrong_password(self):
        """-40209 must never end the retries.

        docs/protocol-notes.md:131 establishes it as this hub's reply to a
        method that exists and was called wrongly -- it is how the face and
        battery methods were proved absent -- and it is the siren's volume
        refusal besides. The only reachable route to H500AuthError is a coded
        refusal out of Tapo.__init__'s own getBasicInfo call, so trusting
        -40209 would aim that route at a shape mismatch and show a "check your
        password" notice to somebody whose password is fine.
        """
        self.assertFalse(api.is_auth_failure(Exception(
            'Error: Invalid parameters, Response: {"error_code": -40209}')))
        self.assertNotIn(-40209, api.AUTH_ERROR_CODES)

    def test_a_retryable_error_code_still_retries(self):
        """-40401 is an expired stok, which pytapo re-logs-in for itself."""
        self.assertFalse(api.is_auth_failure(Exception(
            'Error: Invalid stok value, Response: {"error_code": -40401}')))

    def test_a_nested_error_code_is_not_mistaken_for_the_refusal(self):
        """The hub reports per-request codes as well as its own.

        A multi-request answer carries an error_code inside result.responses[]
        and the hub's own at the top level, and pytapo raises on the top-level
        one. Reading whichever code appears first in the text picks the nested
        one, which is the fail-dangerous direction: a body the hub did not
        refuse on ends the retries and blames the owner's password.
        """
        self.assertFalse(api.is_auth_failure(Exception(
            'Error: Request failed, Response: '
            '{"result": {"responses": [{"error_code": -40414}]}, '
            '"error_code": 0}')))
        # ...and the same body shape must still be read as a refusal when the
        # refusal is the hub's own.
        self.assertTrue(api.is_auth_failure(Exception(
            'Error: Need login by local password, Response: '
            '{"result": {"responses": [{"error_code": 0}]}, '
            '"error_code": -40414}')))

    def test_an_unreadable_body_retries(self):
        """No parseable code is not evidence of a refusal."""
        for err in (Exception("Error: boom, Response: not json at all"),
                    Exception("no response marker here"),
                    Exception('Error: x, Response: ["error_code", -40414]')):
            with self.subTest(text=str(err)):
                self.assertFalse(api.is_auth_failure(err))


def _setup_function(name: str) -> ast.AST:
    for node in ast.walk(SETUP):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    raise AssertionError(f"{name} is gone")


def _names(node: ast.AST) -> set:
    return {inner.id for inner in ast.walk(node) if isinstance(inner, ast.Name)}


def _connect_try() -> ast.Try:
    """The try in async_setup_entry whose body performs the login."""
    for node in ast.walk(_setup_function("async_setup_entry")):
        if isinstance(node, ast.Try) and any(
                "connect" in {getattr(inner, "attr", "")
                              for inner in ast.walk(statement)
                              if isinstance(inner, ast.Attribute)}
                for statement in node.body):
            return node
    raise AssertionError("the connect() call is no longer inside a try")


# What setup does with a failed connect is driven in
# test_setup_entry.FailurePaths: a refused credential asks for a new password,
# four other failure shapes all schedule a retry instead, and every path
# closes the login it opened. Reading the handlers out of the syntax tree
# said the code was arranged correctly; running them says it behaves.


class Reauth(unittest.TestCase):
    # What the form holds and what it saves are driven in
    # test_reconfigure.TheReauthForm -- the step is constructed, the form
    # submitted, and the entry read back. What is left here is the one thing
    # a driven flow cannot show, because it is about the source: that there
    # is only one login in the file.

    def test_one_validation_path_serves_both_forms(self):
        """Two copies of the login would sooner or later mean two logins, and
        this hub wedges under repeated ones."""
        self.assertIn("def _validate", SOURCE)
        self.assertEqual(SOURCE.count("H500Client("), 1)


class ReauthLabels(unittest.TestCase):
    def test_every_reauth_field_has_a_label_in_both_files(self):
        fields = _schema_fields("async_step_reauth_confirm")
        for name, strings in LABEL_FILES.items():
            with self.subTest(file=name):
                labelled = set(
                    strings["config"]["step"]["reauth_confirm"]["data"])
                self.assertEqual(fields - labelled, set())

    def test_the_new_error_and_abort_keys_exist_in_both_files(self):
        """Unlabelled, the form's failure reads as "invalid_auth" and its
        success as "reauth_successful"."""
        for name, strings in LABEL_FILES.items():
            with self.subTest(file=name):
                self.assertIn("invalid_auth", strings["config"]["error"])
                self.assertIn("reauth_successful", strings["config"]["abort"])


if __name__ == "__main__":
    unittest.main()

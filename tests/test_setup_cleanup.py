"""A setup that fails must not leave a login behind.

``async_setup_entry`` connects, then asks the coordinator for its first
refresh. Connecting is a login; the first refresh is the first poll, and it
raises ``ConfigEntryNotReady`` if the hub does not answer. Home Assistant then
retries the whole of setup on a backoff -- so a hub that is briefly unhappy
gets a fresh login per retry, none of which is ever closed.

This hub is known to wedge under repeated logins and to recover only on a
timeout, which makes an unclosed session per retry the exact shape of failure
that turns a momentary hiccup into a hub that has to be power-cycled. The
connect() path above already closes on failure; this is the same rule one
statement later.

Asserted through the syntax tree rather than the source text. A test that
greps for "client.close" is satisfied by a comment mentioning it, which is a
trap this repository has already fallen into once.
"""
import ast
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
TREE = ast.parse((COMPONENT / "__init__.py").read_text())


def function(name: str) -> ast.AST:
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    raise AssertionError(f"{name} is gone")


def touches(node: ast.AST) -> set[str]:
    """Every name used anywhere under this node.

    Called or merely referenced, because both spellings do the same thing
    here: ``client.close()`` directly, or ``client.close`` handed to
    ``async_add_executor_job`` because it blocks.
    """
    found = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Attribute):
            found.add(inner.attr)
        elif isinstance(inner, ast.Name):
            found.add(inner.id)
    return found


def guarding(name: str) -> ast.Try:
    """The try block whose body uses `name`."""
    for node in ast.walk(function("async_setup_entry")):
        if isinstance(node, ast.Try) and any(
                name in touches(statement) for statement in node.body):
            return node
    raise AssertionError(f"{name} is not inside a try block")


class SetupCleanup(unittest.TestCase):
    def test_a_failed_first_refresh_closes_the_client(self):
        block = guarding("async_config_entry_first_refresh")
        closed = any("close" in touches(handler) for handler in block.handlers)
        self.assertTrue(closed,
                        "a failed first refresh leaks the login it just made")

    def test_it_still_reports_the_failure(self):
        """Closing must not swallow ConfigEntryNotReady -- Home Assistant
        needs the exception to schedule the retry at all."""
        block = guarding("async_config_entry_first_refresh")
        for handler in block.handlers:
            raised = [node for node in ast.walk(handler)
                      if isinstance(node, ast.Raise)]
            self.assertTrue(raised, "the failure was caught and dropped")

    def test_a_failed_connect_still_closes_too(self):
        """The case that was already right, so a refactor cannot undo it."""
        block = guarding("connect")
        self.assertTrue(any("close" in touches(handler)
                            for handler in block.handlers))

    def test_nothing_is_closed_on_the_success_path(self):
        """The client is the live hub connection for the entry's whole life."""
        block = guarding("async_config_entry_first_refresh")
        self.assertNotIn("close", touches(ast.Module(body=block.body,
                                                   type_ignores=[])))


if __name__ == "__main__":
    unittest.main()

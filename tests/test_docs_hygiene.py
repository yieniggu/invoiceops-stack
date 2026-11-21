"""Copy-paste hygiene of the docs.

zsh (the default macOS shell) does not treat `#` as a comment in an interactive session, so a
pasted command with a trailing `# explanation` fails or misbehaves. Shell code blocks therefore
carry NO comments (explanations go in the text around the block), and runbooks define shell
functions instead of command-holding variables (`STACK=...`, then `$STACK ...`).
"""

import re
import unittest

from support import ROOT, TICKETS, V2

SHELL_LANGS = {"", "bash", "sh", "zsh", "shell", "console"}
FENCE = re.compile(r"^\s*```(\w*)\s*$")
HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")
QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
COMMENT = re.compile(r"(?:^|\s)#")
# A variable that holds a command line (`STACK="docker compose ..."`, then `$STACK ps`).
COMMAND_VARIABLES = re.compile(r"^\s*(?:export\s+)?[A-Za-z_]\w*=[\"']?(?:docker |env )")
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def shell_command_lines(text):
    """Yield (line number, line) for every command line inside a shell fenced block.

    Heredoc bodies (file content written by the command, not commands) are skipped.
    """
    in_block, lang, heredoc_end = False, "", None
    for number, line in enumerate(text.splitlines(), 1):
        fence = FENCE.match(line)
        if fence and heredoc_end is None:
            in_block = not in_block
            lang = fence.group(1)
            continue
        if not in_block or lang not in SHELL_LANGS:
            continue
        if heredoc_end is not None:
            if line.strip() == heredoc_end:
                heredoc_end = None
            continue
        yield number, line
        started = HEREDOC.search(line)
        if started:
            heredoc_end = started.group(1)


def shell_comment_lines(text):
    """`#` comments (full line or trailing) in command lines; quoted hashes and `${X#...}` are fine."""
    return [(n, line) for n, line in shell_command_lines(text) if COMMENT.search(QUOTED.sub("''", line))]


def shell_variable_lines(text):
    return [(n, line) for n, line in shell_command_lines(text) if COMMAND_VARIABLES.search(line)]


INLINE = re.compile(r"`([^`\n]+)`")
COPY_TO_HOME = re.compile(r"\bcp\s+(?:-\w+\s+)*\S+\s+(~/[^\s/]+)(?:/\S*)?")


def inline_copy_without_mkdir(text):
    """Inline `cp` snippets that copy into a `~/dir` the same snippet does not create first."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for snippet in INLINE.findall(line):
            copy = COPY_TO_HOME.search(snippet)
            if copy and f"mkdir -p {copy.group(1)}" not in snippet[: copy.start()]:
                found.append((number, snippet))
    return found


def docs():
    mlflow = V2 / "invoiceops-mlflow"
    files = sorted(ROOT.glob("*.md")) + [V2 / "invoiceops-ml" / "README.md"]
    files += sorted(mlflow.glob("*.md")) + sorted((mlflow / "gateway").glob("*.md"))
    if TICKETS.is_dir():
        files += sorted(TICKETS.rglob("*.md"))
    return [path for path in files if path.is_file()]


FENCE_MARK = "`" * 3


def block(lang, *lines):
    return "\n".join([FENCE_MARK + lang, *lines, FENCE_MARK, ""])


class ScannerTests(unittest.TestCase):
    def test_it_flags_inline_and_full_line_comments_but_not_quoted_hashes_or_expansions(self):
        text = block("bash", "ls   # inline", "# full line", "echo '#no' \"#no\"", "printf '%s' \"${X#*@}\"")
        self.assertEqual([line for _, line in shell_comment_lines(text)], ["ls   # inline", "# full line"])

    def test_it_skips_heredoc_bodies_and_non_shell_blocks(self):
        text = block("bash", "cat > f <<'EOF'", "#!/bin/sh", "# body", "EOF", "ls") + block("text", "# fine")
        self.assertEqual(list(shell_comment_lines(text)), [])

    def test_it_flags_the_command_holding_variables(self):
        text = block("bash", 'STACK="docker compose"', 'export X="env -u A b"', 'APP="$PWD"', "stack ps")
        self.assertEqual([number for number, _ in shell_variable_lines(text)], [2, 3])


class InlineScannerTests(unittest.TestCase):
    def test_it_flags_an_inline_copy_into_a_home_folder_without_mkdir(self):
        text = "paso (`cp -R notebooks ~/curso/notebooks`) y `ls`"
        self.assertEqual([s for _, s in inline_copy_without_mkdir(text)], ["cp -R notebooks ~/curso/notebooks"])

    def test_it_accepts_the_copy_after_mkdir_of_the_same_folder(self):
        self.assertEqual(inline_copy_without_mkdir("`mkdir -p ~/curso && cp -R a ~/curso/a`"), [])
        self.assertEqual(len(inline_copy_without_mkdir("`mkdir -p ~/otro && cp -R a ~/curso/a`")), 1)


class DocsHygieneTests(unittest.TestCase):
    def test_there_are_docs_to_scan(self):
        names = {path.name for path in docs()}
        self.assertTrue({"README.md", "LOCAL_E2E.md", "RECOVERY_RUNBOOK.md"} <= names)

    def test_no_shell_block_has_a_hash_comment(self):
        found = [f"{path.relative_to(V2)}:{number}: {line.strip()[:70]}"
                 for path in docs() for number, line in shell_comment_lines(path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "move the explanation out of the copy-paste block")

    def test_inline_copies_into_a_home_folder_create_it_first(self):
        found = [f"{path.relative_to(V2)}:{number}: {snippet}"
                 for path in docs() for number, snippet in inline_copy_without_mkdir(path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "prepend `mkdir -p <folder> &&` to the inline command")

    def test_the_students_copy_step_of_the_ml_readme_creates_the_folder(self):
        readme = (V2 / "invoiceops-ml" / "README.md").read_text(encoding="utf-8")
        self.assertIn("`mkdir -p ~/invoiceops-curso && cp -R notebooks ~/invoiceops-curso/notebooks`", readme)

    def test_runbooks_use_shell_functions_not_command_variables(self):
        found = [f"{path.relative_to(V2)}:{number}: {line.strip()[:70]}"
                 for path in docs() for number, line in shell_variable_lines(path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [])


@unittest.skipUnless(TICKETS.is_dir(), "dev/tickets is not next to the repositories (it is not a repository)")
class TicketsTests(unittest.TestCase):
    def test_relative_links_in_tickets_resolve(self):
        broken = []
        for path in TICKETS.rglob("*.md"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for target in LINK.findall(line):
                    if re.match(r"\w+://|mailto:", target):
                        continue
                    if not (path.parent / target).exists():
                        broken.append(f"{path.relative_to(TICKETS)}:{number}: {target}")
        self.assertEqual(broken, [])

    def test_every_superseded_ticket_starts_with_the_replacement(self):
        files = sorted((TICKETS / "superseded").glob("*.md"))
        self.assertGreaterEqual(len(files), 13)
        for path in files:
            first = path.read_text(encoding="utf-8").splitlines()[0]
            self.assertRegex(first, r"^Reemplazado por MLOPS-\d{2}\b", path.name)


if __name__ == "__main__":
    unittest.main()

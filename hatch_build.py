"""Build hook: the README as the package index shows it.

In the repository the README links to its pictures and to the other documents by
relative path. The package index shows the README alone, so those links are made
full addresses when the package is built; the file itself stays as it is.
"""

import os
import re

try:
    from hatchling.metadata.plugin.interface import MetadataHookInterface
except ImportError:                     # read outside a build (the tests): only `for_the_index` is used
    MetadataHookInterface = object

REPO = "https://github.com/edacommons/FastDRC"
RAW = "https://raw.githubusercontent.com/edacommons/FastDRC/main/"


def for_the_index(text: str) -> str:
    """`text` with every relative link made a full address: a picture to the file
    itself, a document to its page, a folder to its listing."""
    def link(m):
        bang, label, target = m.group(1), m.group(2), m.group(3)
        if re.match(r"[a-z]+:|#", target):
            return m.group(0)
        if bang:
            return f"![{label}]({RAW}{target})"
        kind = "blob" if os.path.splitext(target.split("#")[0])[1] or target == "LICENSE" else "tree"
        return f"[{label}]({REPO}/{kind}/main/{target})"
    return re.sub(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)", link, text)


class ReadmeHook(MetadataHookInterface):
    def update(self, metadata):
        with open(os.path.join(self.root, "README.md"), encoding="utf-8") as f:
            metadata["readme"] = {"content-type": "text/markdown", "text": for_the_index(f.read())}

"""Temporary script to rewrite VeribleParser for stdin support."""
import re

path = r"C:\Users\man\Desktop\code\vutils\src\vutils\vparser.py"
with open(path, "r", encoding="utf-8") as f:
    text = f.read()

# Replace __init__ and _parse
old_init = '''    def __init__(self, filepath: str):
        self.filepath = str(Path(filepath).resolve())
        self.source = Path(self.filepath).read_bytes().decode("utf-8")
        self._data: Optional[dict] = None
        self._modules: Optional[list[dict[str, Any]]] = None'''

new_init = '''    def __init__(self, filepath: str = "", source: str = ""):
        if filepath:
            self.filepath = str(Path(filepath).resolve())
            self.source = Path(self.filepath).read_bytes().decode("utf-8")
            self._use_stdin = False
        elif source:
            self.filepath = "<stdin>"
            self.source = source
            self._use_stdin = True
        else:
            raise ValueError("Provide either filepath or source")
        self._data: Optional[dict] = None
        self._modules: Optional[list[dict[str, Any]]] = None'''

assert old_init in text, "old __init__ not found"
text = text.replace(old_init, new_init, 1)

old_parse = '''    def _parse(self) -> None:
        result = subprocess.run(
            [VERIBLE, "--export_json", "--printtree", self.filepath],
            capture_output=True, text=True,
        )'''

new_parse = '''    def _parse(self) -> None:
        if self._use_stdin:
            result = subprocess.run(
                [VERIBLE, "--export_json", "--printtree", "-"],
                input=self.source.encode("utf-8"),
                capture_output=True, text=True,
            )
        else:
            result = subprocess.run(
                [VERIBLE, "--export_json", "--printtree", self.filepath],
                capture_output=True, text=True,
            )'''

assert old_parse in text, "old _parse not found"
text = text.replace(old_parse, new_parse, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(text)
print("vparser.py updated")

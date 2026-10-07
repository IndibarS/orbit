"""Debconf's documented passthrough protocol bridged to native GUI questions."""

import os
import socket
import tempfile
import threading


class DebconfBridge:
    def __init__(self, ask):
        self.ask = ask
        self.stop = threading.Event()

    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="orbit-debconf-")
        path = self.directory.name + "/socket"
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(path)
        self.server.listen(4)
        self.server.settimeout(0.2)
        self.previous = {key: os.environ.get(key) for key in ("DEBIAN_FRONTEND", "DEBCONF_PIPE")}
        os.environ.update(DEBIAN_FRONTEND="passthrough", DEBCONF_PIPE=path)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return self

    def run(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with connection:
                connection.settimeout(300)
                try:
                    self.serve(connection.makefile("rw", encoding="utf-8", newline="\n"))
                except (OSError, ValueError):
                    continue

    def serve(self, stream):
        fields, values, pending = {}, {}, []
        title = "Package configuration"
        while not self.stop.is_set():
            line = stream.readline(65537)
            if not line or len(line) > 65536:
                return
            command, _, arguments = line.rstrip("\n").partition(" ")
            answer = "0"
            if command == "DATA":
                tag, key, value = arguments.split(" ", 2)
                fields.setdefault(tag, {})[key] = value.replace("\\n", "\n")
            elif command == "SET":
                tag, _, value = arguments.partition(" ")
                values[tag] = value
            elif command == "GET":
                answer = "0 " + values.get(arguments, "")
            elif command == "SUBST":
                tag, variable, value = arguments.split(" ", 2)
                for key, text in fields.get(tag, {}).items():
                    fields[tag][key] = text.replace("${" + variable + "}", value)
            elif command == "INPUT":
                _, _, tag = arguments.partition(" ")
                pending.append(tag)
            elif command == "TITLE":
                title = arguments
            elif command == "SETTITLE":
                title = fields.get(arguments, {}).get("description", title)
            elif command == "GO":
                for tag in pending:
                    question = fields.get(tag, {})
                    value = self.ask(
                        {
                            "title": title,
                            "kind": question.get("type", "string"),
                            "description": question.get("description", tag),
                            "details": question.get("extended_description", ""),
                            "choices": question.get("choices", ""),
                            "default": values.get(tag, ""),
                        }
                    )
                    values[tag] = str(value).replace("\n", " ").replace("\r", " ")
                pending.clear()
            elif command not in {"CAPB", "PROGRESS", "VERSION", "CLEAR", "STOP"}:
                answer = "20 Unsupported passthrough command"
            if command == "CLEAR":
                pending.clear()
            stream.write(answer + "\n")
            stream.flush()
            if command == "STOP":
                return

    def __exit__(self, *_):
        self.stop.set()
        self.server.close()
        self.thread.join(timeout=1)
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.directory.cleanup()

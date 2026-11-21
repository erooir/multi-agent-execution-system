"""Load file-based credentials into vLLM's native authentication, without CLI secrets."""

from __future__ import annotations

import json
import re
from pathlib import Path


class HiddenKey(str):
    def __repr__(self):
        # vLLM 0.19.1 logs the Namespace using repr; retain real str/encode
        # behavior for its native authentication while hiding startup values.
        return "'<redacted>'"


def load_keys(path: Path) -> list[HiddenKey]:
    content = path.read_text().strip()
    try:
        values = json.loads(content) if content.startswith("[") else [content]
    except ValueError:
        raise ValueError("Invalid model API key file") from None
    if (
        not isinstance(values, list)
        or not 1 <= len(values) <= 32
        or any(
            type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", value) for value in values
        )
        or len(set(values)) != len(values)
    ):
        raise ValueError("Model API key file must contain 1-32 unique valid keys")
    return [HiddenKey(value) for value in values]


def main():
    from vllm.entrypoints.cli.serve import ServeSubcommand
    from vllm.entrypoints.utils import cli_env_setup
    from vllm.utils.argparse_utils import FlexibleArgumentParser

    cli_env_setup()
    parser = FlexibleArgumentParser(description="Native vLLM serve with file-based API keys")
    commands = parser.add_subparsers(required=True, dest="subparser")
    serve = ServeSubcommand()
    serve.subparser_init(commands)
    args = parser.parse_args()
    if args.api_key:
        parser.error("Supply API keys through the mounted secret file, not command-line flags")
    args.api_key = load_keys(Path("/run/secrets/model_api_key"))
    serve.validate(args)
    serve.cmd(args)


if __name__ == "__main__":
    main()

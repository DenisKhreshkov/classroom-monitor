"""Run locally, never in GitHub Actions."""

import argparse

from config import Config, google_scopes
from errors import MonitorError
from google_authentication import credentials


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Authorize Google and save a private refresh token."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--console",
        action="store_const",
        dest="method",
        const="console",
        help="Print the sign-in link and paste the full browser redirect URL (default; no local server).",
    )
    mode.add_argument(
        "--local-server",
        action="store_const",
        dest="method",
        const="local-server",
        help="Open a browser and automatically receive the redirect on a loopback server.",
    )
    parser.set_defaults(method="console")
    args = parser.parse_args(argv)
    try:
        # This local setup command uses the user's current .env rather than stale
        # exported flags from an earlier authorization attempt.
        config = Config.load(auth_only=True, dotenv_override=True)
        print(
            f"OAuth configuration: READ_ATTACHMENTS={str(config.read_attachments).lower()}, "
            f"TEACHER_MODE={str(config.teacher_mode).lower()}"
        )
        print("Requesting these read-only Google scopes:")
        for scope in google_scopes(config):
            print(f"  {scope}")
        credentials(config, interactive=True, authorization_method=args.method)
        print(f"Authorized. Token saved to {config.token_file}")
        print("Keep it private. Add its entire contents as the GOOGLE_TOKEN_JSON GitHub secret.")
        return 0
    except MonitorError as exc:
        print(str(exc))
        return 1
    except KeyboardInterrupt:
        print("Authorization cancelled. Run the command again when ready.")
        return 1
    except Exception as exc:
        print(
            f"Authorization failed ({type(exc).__name__}). Check the Desktop OAuth client and browser consent settings. Provider responses were omitted to protect credentials."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

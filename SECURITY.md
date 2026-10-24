# Security

Never commit exchange credentials, account exports, signed URLs, raw logs or local configuration. Use environment variables or an ignored `.env` file. Credentials exposed in chat or source history should be revoked and replaced before further use.

The default configuration uses public market data and paper signals. Legacy live-execution modules contain real order and cancellation methods. They are experimental, and protection maintenance can write without a separate confirmation dialog. Tests and CI must remain offline.

Generated output belongs in ignored `data/` or `reports/`. Only reviewed, sanitized documents belong in `records/`. Run `python scripts/check_publication.py` and Gitleaks before publishing changes.

Report suspected vulnerabilities without secrets or account data. Use GitHub private vulnerability reporting when available; otherwise ask the maintainer for a private contact route before sending details.

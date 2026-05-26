# USCCB to EPUB

Scrape the USCCB daily mass readings page, build EPUB files, and serve them in an OPDS catalog.

## Static OPDS for GitHub Pages

Refresh the rolling reading window and publish a static catalog:

```bash
python -m usccb_to_epub sync
python -m usccb_to_epub publish-static --site-url https://YOUR_USER.github.io/YOUR_REPO
```

This writes:

- `docs/index.html`
- `docs/opds.xml`
- `docs/books/*.epub`

The published catalog groups entries into Daily Readings, Sunday Readings, and Special/Optional Readings.
Sundays appear in both the Daily and Sunday sections.
The rolling window is computed in `America/New_York` and includes the previous 7 days through the next 14 days, inclusive.
The sync step prunes generated EPUB/JSON files outside that window from `data/library/`, and publishing rewrites `docs/books/` from the current library.

To publish on GitHub Pages:

1. Commit and push your repository.
2. In GitHub, open Settings > Pages and set Source to `GitHub Actions`.
3. The workflow in `.github/workflows/deploy-pages.yml` will refresh the library and publish `docs/`.

Your OPDS feed URL will be:

`https://YOUR_USER.github.io/YOUR_REPO/opds.xml`

## Usage

Build today's readings:

```bash
python -m usccb_to_epub build
```

Build a specific date:

```bash
python -m usccb_to_epub build --date 2026-05-31
```

Refresh the default rolling window:

```bash
python -m usccb_to_epub sync
```

Refresh a custom window around a specific anchor date:

```bash
python -m usccb_to_epub sync --date 2026-05-31 --past-days 3 --future-days 10
```

If matching `data/library/*.json` metadata files already exist, build and sync commands reuse cached readings metadata unless `--refresh` is supplied.
When USCCB pages expose multiple Mass variants for a single date, the tool generates one EPUB per Mass title. Generated files are named with the date and Mass name.

Serve an OPDS catalog:

```bash
python -m usccb_to_epub serve --host 127.0.0.1 --port 8000
```

The catalog is available at `/opds.xml`, and generated EPUB files are served from `/books/`.

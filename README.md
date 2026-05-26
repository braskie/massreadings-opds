# USCCB to EPUB

Scrape the USCCB daily mass readings page, build EPUB files, and serve them in an OPDS catalog.

## Static OPDS for GitHub Pages

Generate a static catalog and copy EPUB files into `docs/`:

```bash
python -m usccb_to_epub publish-static --site-url https://YOUR_USER.github.io/YOUR_REPO
```

This writes:

- `docs/index.html`
- `docs/opds.xml`
- `docs/books/*.epub`

To publish on GitHub Pages:

1. Commit and push your repository, including `data/library` book files.
2. In GitHub, open Settings > Pages and set Source to `GitHub Actions`.
3. The workflow in `.github/workflows/deploy-pages.yml` will publish `docs/`.

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

Serve an OPDS catalog:

```bash
python -m usccb_to_epub serve --host 127.0.0.1 --port 8000
```

The catalog is available at `/opds.xml`, and generated EPUB files are served from `/books/`.

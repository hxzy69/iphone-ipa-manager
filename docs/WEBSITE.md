# Project website

The public site is https://hxzy69.github.io/iphone-ipa-manager/.
It is a static HTML/CSS/JavaScript site in `docs/site`, with no build dependencies,
analytics, external font requests, cookies, or client-side API requirements.
GitHub Pages hosting remains subject to GitHub's privacy policy.

## Preview and validate

```sh
python tools/check_website.py
python -m http.server 8877 --directory docs/site
```

Open `http://localhost:8877`. Check narrow and wide viewports, keyboard navigation,
gallery switching, image enlargement/Escape, command search, FAQ expansion, and
download links. Core content and downloads work without JavaScript.

## Screenshots

The overview, accounts, and connections images are real captures of
`tools/preview_gui.py` from version 0.3.1. They show explicit sample data, not
the maintainer's live profile. Capture only the preview window. Never publish
personal account emails, device identifiers, paths, tokens, or desktop content.
The sample status shown is illustrative, not evidence of device connectivity.

`assets/mark.svg` is the site's phone/checkmark graphic. It is not an Apple logo.

## Publishing

The repository's Pages source is GitHub Actions. The `Project website` workflow
validates changes on pull requests, and publishes only `docs/site` on changes to
main or manual dispatch. Application source, private state, and release archives
are not part of the Pages payload. Third-party actions are pinned to commit IDs.

When publishing a new app release, update the version labels and direct asset
links in `index.html` after verifying that release. Keep the latest-release link
as the canonical way to find newer builds. Update screenshots when the UI changes.
Do not claim untested performance or remove connection/Apple-limit disclosures.

# Omaplex: current spec

## Repository and plugin identity

- Repository: `pjgeutjens/omarchy-omaplex`
- Local source: `~/personal/local-tools/omarchy-omaplex`
- Proposed plugin ID: `io.github.pjgeutjens.omaplex`
- Omarchy kind: `bar-widget`, with a panel opened by the bar widget
- First target: Omarchy Quattro and its current schema version 1 plugin API

The plugin provides compact Continue Watching and recently added lists, a fullscreen library browser, and `mpv` playback that reports progress to Plex.

## First release

Version 0.2:

- show a compact new-item count or Plex glyph in the bar;
- opens a text-only panel with no posters;
- includes Continue Watching, Added, Movies, Shows, and Browse All modes;
- combine both media types by Plex `addedAt`, newest first;
- collapse multiple recent episodes of the same show into one show entry;
- label a completely unwatched item added within the last 30 days as new;
- distinguish unwatched, started, and watched items;
- opens Browse All as a fullscreen searchable and paged panel;
- streams playable items through `mpv` in windowed or fullscreen mode;
- remembers the last visible windowed player rectangle and restores it through Hyprland;
- reports playback progress and watched completion to Plex;
- lets the user mark any compact-list item watched or unwatched;
- keeps Plex Web as an explicit item action;
- discovers and scans every movie and show library, with explicit confirmation that Plex accepted the request rather than claiming the scan finished;
- exposes `open`, `close`, `toggle`, `refresh`, `scan`, `settings`, and `status` through Omarchy Shell IPC.

“Recently added” means added to this Plex server. It does not mean a new cinema, streaming, or broadcast release.

## Interaction

- Left click opens or closes the panel.
- Middle click refreshes the list.
- Right click opens Connection settings.
- The bar count covers new, unwatched additions inside the configured age window.
- `[` and `]` move between the four compact views; Omarchy retains `h` and `l` for panel switching.
- `/` searches the cached rows in the current compact view; Browse All keeps its separate library-wide search.
- Up and Down or `j` and `k` move the selection.
- Enter plays the selected item in the chosen window mode.
- `x` toggles the selected item between watched and unwatched; the watch-state badge performs the same action.
- `c`, `a`, `m`, and `s` select Continue, Added, Movies, and Shows.
- `b` opens Browse All, `t` toggles watched items, `o` opens Plex Web, and `,` opens settings.
- `r` refreshes, `u` requests scans for all video libraries, and Escape closes the active view.

The first panel paint uses cached metadata. A Plex request must not delay panel opening.

## Plex API

Use Plex directly. Tautulli is not required.

At setup, discover every movie and TV library rather than assuming section IDs. DailyDash was an API reference, not a runtime dependency:

- movies: `/library/sections/<id>/recentlyAdded?type=1`;
- episodes: `/library/sections/<id>/recentlyAdded?type=4`;
- scan: `/library/sections/<id>/refresh`;
- authentication: `X-Plex-Token` request header.

Normalize each entry to a small model containing rating keys, media kind, title, subtitle, added time, playback hint, and watch state. Cap response bytes, item counts, and string lengths.

## Configuration and credentials

Required setup values are a Plex server origin and `X-Plex-Token`. Selected library section IDs and display settings are non-secret.

- Open an in-panel connection form automatically when no configuration exists.
- Accept the token only through bounded stdin to the helper.
- Store the token in the user's secret service through `secret-tool`.
- Send the token in a request header, never in a URL, process argument, log, IPC response, or `shell.json`.
- Restrict the server setting to an HTTP or HTTPS origin without a path, query, or fragment.
- Do not follow authenticated redirects to another origin.
- Permit plain HTTP for a user-confirmed trusted LAN server, with a clear warning in setup.
- Test the server and token before replacing a working configuration.

The plugin uses a small helper for Plex HTTP, JSON parsing, caching, playback proxying, and browser URL construction. QML consumes strict bounded JSON.

## Cache and refresh

- default metadata refresh: 15 minutes;
- manual refresh is always available;
- one metadata refresh at a time;
- keep the last successful list if Plex is offline;
- write one bounded metadata snapshot atomically with private permissions;
- label cached and offline states accurately;
- a scan request returns `accepted` only after every selected library returns success;
- refresh the recent list after a scan request, but do not claim indexing is complete.

## Proposed files

```text
manifest.json
BarWidget.qml
Panel.qml
PlexState.qml
Model.js
bin/omaplex
tests/
scripts/validate.sh
README.md
LICENSE
```

The helper should use Python 3.11 or another dependency already present on Omarchy. It should not install packages or download code on first run.

## Tests

Cover these cases before the first local install:

- library discovery and selection for one or more movie and TV sections;
- movie and episode normalization;
- duplicate episodes collapse to one show entry;
- chronological merge and page boundaries;
- new badge only for completely unwatched items inside the configured age window;
- started and watched state handling;
- empty libraries, offline server, invalid token, timeout, and malformed response;
- bounded status, browse, setup, and cache documents;
- token absence from argv, logs, settings, cache metadata, URLs, and IPC output;
- scan acceptance versus scan completion wording;
- keyboard navigation and panel close behavior;
- QML validation and live shell load.

## Local completion gate

Version 0.2 is locally complete when:

1. `omarchy plugin validate .` passes.
2. Every QML file passes `qmllint -I "$OMARCHY_PATH/shell"`.
3. Helper and model tests pass against recorded fixtures without a live Plex server.
4. A local plugin copy loads in the running Omarchy shell.
5. The panel opens from cache while Plex is deliberately unreachable.
6. Continue Watching and recent additions are observed from the configured Plex server.
7. A real scan request is accepted and the UI does not claim completion early.
8. Disable, re-enable, update, and removal behavior are checked.

GitHub publication and marketplace submission come after this local gate.

## Publication fit

This plugin is a native Omarchy bar experience rather than a standalone miniplayer. It combines quick activity lists, a full library browser, secure local playback, Plex timeline updates, and standalone setup without Tautulli or DailyDash.

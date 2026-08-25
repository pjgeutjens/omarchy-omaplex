# TODO

Dogfood the current plugin before expanding it. Record actual playback failures and repeated manual work. Keep the compact, text-first interface.

## Next candidates

- [ ] Add a playback fallback path:
  - Detect a missing `mpv` before launch and present an `Open in Plex Web` action.
  - Try direct playback first.
  - If direct playback fails because of media compatibility, request a Plex HLS transcode and retry.
  - If both fail, present the Plex Web action with a useful error.
  - Keep the token out of media URLs and process arguments.
- [ ] Add optional next-episode playback:
  - Refresh Plex On Deck after an episode finishes.
  - Offer the next episode instead of forcing autoplay.
  - Allow autoplay later as an explicit preference.

## Playback lifecycle

- [ ] Pause playback when Omarchy locks or the computer suspends.
- [ ] Send a final Plex timeline update after a normal exit, player failure, or network disconnect.
- [ ] Make failed Plex timeline updates visible without interrupting playback.
- [ ] Show whether playback is direct or transcoded, plus resolution, codecs, audio layout, and bitrate when Plex supplies them.

## Item actions

- [ ] Add a compact action menu for:
  - Play from the beginning.
  - Mark watched or unwatched.
  - Open in Plex Web.
  - Refresh the relevant library.
  - Remove an item from Continue Watching when Plex supports it.

## Remembered preferences

- [ ] Remember Windowed or Fullscreen mode.
- [ ] Remember preferred audio and subtitle languages.
- [ ] Remember whether subtitles are enabled.
- [x] Remember the last windowed player position and size, restoring the position only when it remains visible on a connected monitor.

## Development safety

- [ ] Add a `--no-plex-sync` playback mode for live player tests.
- [ ] Make test mode visibly distinct so it cannot be mistaken for ordinary playback.
- [ ] Keep live playback tests limited to confirmed watched episodes.

## Standalone setup

- [x] Make first-run setup work entirely inside the plugin.
- [x] Remove DailyDash and external `.env` files from the required setup path.
- [x] Keep `configure-from-env` only as an optional import tool for development or migration.
- [x] Add an in-panel connection form with:
  - Plex server URL.
  - Password-style token input.
  - Test and Save.
  - Discovered movie and show libraries.
  - Connection status and last successful refresh.
  - Clear credentials.
- [x] Send the token to the helper over stdin and store it in the desktop Secret Service.
- [x] Never put the token in Omarchy settings, process arguments, logs, or cache files.
- [ ] Check required and optional dependencies in the UI.
- [ ] Test first launch from an empty config and cache directory.

## Publication

- [ ] Put the project in Git and create a tagged release.
- [ ] Test clean installation, upgrade, removal, and credential cleanup.
- [ ] Run the Ryan-style audit against the exact release commit.

## Out of scope for now

- Posters in the compact panel.
- An embedded player.
- More permanent top-level modes.
- Recommendations and discovery feeds.
- Multi-server support.
- Manual transcode-quality controls unless automatic fallback proves insufficient.

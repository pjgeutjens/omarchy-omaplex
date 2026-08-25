# Plex for Omarchy

A right-side Omarchy bar widget with bounded Continue Watching, combined Recently Added, Recent Movies, and Recent Series lists. Every row says `Movie` or `Series` and has an explicit `UNWATCHED`, `STARTED`, or `WATCHED` label. Continue Watching merges Plex's in-progress and On Deck items, deduplicates each series, and sorts them newest-viewed first. Combined Recently Added is newest-added first. The separate movie and series views prioritize unfinished items. Multiple newly added episodes from one show collapse into the newest show row.

Series rows use the matching Plex On Deck episode when one is available. Its play action resumes the current episode or starts the next one.

Click a row to stream it through `mpv`. Choose Windowed for a normal floating, movable window or Fullscreen before playback. Windowed mode remembers its last compositor position and size for the next launch; if that rectangle is no longer visible on a connected monitor, only the saved size is used. Press `O` to open the same item in Plex Web.

Browse All opens a separate fullscreen Omarchy panel. It searches and pages through the complete movie or series library without loading the full catalogue into the bar popup. Opening a series drills into its episodes.

`mpv` keeps its built-in on-screen controller and default keyboard bindings. Use Space for pause, the arrow keys to seek, `#` to choose an audio track, `J` to choose a subtitle track, and `F` to toggle fullscreen. These track controls cover audio and embedded subtitles in the selected media part; Plex sidecar subtitle tracks are also sent to `mpv` through the private loopback proxy.

## Requirements

- Omarchy Quattro with the schema version 1 plugin API
- Python 3.11 or newer
- `secret-tool`, `fzf`, `mpv`, and `xdg-open`
- A Plex server origin and `X-Plex-Token`

## Local install

Copy or symlink this repository to:

```text
~/.config/omarchy/plugins/io.github.pjgeutjens.plex-recently-added
```

Then add `io.github.pjgeutjens.plex-recently-added` to the right section of `~/.config/omarchy/shell.json`. Saved user plugin files and `shell.json` changes reload automatically. A manual rescan is also available:

```bash
omarchy-shell shell rescanPlugins
```

## First-run setup

Open the Plex widget after installation. On first launch it opens Connection settings automatically:

1. Enter the Plex server origin, such as `http://plex-server:32400`.
2. Paste an `X-Plex-Token`.
3. Select **Test and save**.

The connection is tested before anything is replaced. The plugin discovers every movie and series library on the server, saves their section IDs, and immediately loads the lists. Open Connection settings later with `,` or the small settings button in the panel footer. When editing a working connection, leave the token blank to keep the saved token.

The server origin and discovered section IDs go to `~/.config/plex-recently-added/config.json`; the last windowed player rectangle goes to `player-window.json` in the same private directory. The token goes to the desktop secret service. It isn't written to Omarchy settings, cache files, URLs, logs, IPC output, or process arguments. Removing credentials from Connection settings requires a confirmation click and clears both the saved token and Plex data while retaining the player geometry preference.

Plain HTTP is allowed for a trusted LAN Plex server. It exposes Plex traffic to that LAN, so use HTTPS for untrusted networks.

### Optional `.env` import

For development or migration, the helper can import the four `PLEX_` values shown in `.env.example`. This is optional; the plugin does not depend on DailyDash or another project's `.env` file.

```bash
~/.config/omarchy/plugins/io.github.pjgeutjens.plex-recently-added/bin/plex-recently-added \
  configure-from-env /path/to/project/.env
```

## Interaction

- Left click: open or close the panel
- Middle click: refresh
- Right click: open Connection settings
- Up/Down or J/K: move the selected row
- Enter or click: play the selected item
- X or click the watch-state badge: toggle the selected item between watched and unwatched
- [ / ]: move between Continue, Added, Movies, and Series (H/L remain available to Omarchy for switching panels)
- /: search the rows in the current compact view
- T or the panel button: show or hide watched items
- C: Continue Watching
- A: combined Recently Added
- M: recently added movies
- S: recently added series
- B or Browse All: open the fullscreen library browser
- ?: toggle the searchable keybindings view
- The keybindings view opens with its search field focused
- , or the footer settings button: open Connection settings
- W: select Windowed playback
- F: select Fullscreen playback
- O: open the selected item in Plex Web
- R: refresh the displayed Plex data
- U or Scan all: discover and scan every movie and series library
- Escape: close the panel

The panel reads `~/.cache/plex-recently-added/recent.json` before it contacts Plex. A failed refresh keeps the last successful list and labels it offline.

Every 15 minutes, the plugin asks Plex for its current library sections and starts a scan for every numeric movie and series section it discovers. It does not assume fixed section IDs. After Plex accepts the asynchronous scan, the plugin refreshes its displayed data twice while the scan settles. `U` triggers the same process immediately; ordinary `R` remains a lightweight data refresh.

In Browse All, select Movies or Series first, then `/` fuzzy-searches only that scope. M/S switch the unfiltered scope, N/P change pages, and Escape goes back or closes the browser. Season syntax applies to Series searches: a query such as `Alone S01` expands matching series into naturally ordered Season 1 episode results; `S01E03` can select one episode directly.

## Playback boundary

The helper resolves the Plex media part, starts a random loopback-only HTTP endpoint, and runs `mpv` against that local URL. The helper adds `X-Plex-Token` when it requests the upstream media. This keeps the token out of `mpv` arguments while retaining Range requests for seeking.

While mpv is open, the helper reports its playback position to Plex every ten seconds and once more when the player closes. Plex can therefore resume later playback from the new position. Reaching 90 percent marks the item watched, and the panel refreshes when playback ends.

The watch-state badge is also an action. Click it, or select a row and press `X`, to send Plex a watched or unwatched update. A started item becomes watched; a watched item becomes unwatched. The panel refreshes after Plex accepts the update.

## Validation

```bash
./scripts/validate.sh
```

This runs Omarchy's plugin validator, `qmllint`, Python tests, and the Node tests for the QML data model.

## Removal

Close any player window first, then remove the widget through Omarchy Plugin Control or run:

```bash
omarchy plugin remove io.github.pjgeutjens.plex-recently-added --yes
```

Removal does not delete the saved server settings, cached list, or secret-service token. Delete them when you want a complete reset:

```bash
secret-tool clear service io.github.pjgeutjens.plex-recently-added
rm -rf ~/.config/plex-recently-added ~/.cache/plex-recently-added
```

The plugin installs no service, privileged file, or Hyprland rule.

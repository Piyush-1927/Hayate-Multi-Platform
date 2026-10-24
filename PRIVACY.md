# Hayate Privacy Policy

Last updated: 24 October 2026

Hayate is a desktop anime app. It has no Hayate server, no accounts and no
advertising. This page explains what data the app handles and where it goes.

## Data stored on your device

Hayate keeps your watch history, library, settings and caches in a folder on
your own computer (for example `~/.config/hayate`). This data never leaves your
device unless you turn on cloud sync below.

## Optional cloud sync

Cloud sync is off by default. If you turn it on, Hayate copies your watch
history and library to storage that you own and control. Nothing is sent to the
developer.

### Google Drive

- Sign-in uses Google's OAuth flow in your own browser. Hayate never sees your
  Google password.
- Hayate requests these permissions:
  - `drive.appdata`: access to one hidden folder that only Hayate can use in
    your Google Drive. Hayate cannot see, read or change any of your other
    files.
  - `openid` and `email`: used only to show which Google account is signed in
    on the Settings page.
- Hayate stores one file there, `hayate_sync.json`, containing your watch
  history (anime titles, episodes watched, playback position) and your library.
- Your sign-in token is stored only on your device, in Hayate's settings folder.
- You can sign out in Settings at any time, which deletes the token from your
  device and revokes Hayate's access. You can also remove Hayate's access in
  your Google Account under Security, then Third-party access. To delete the
  synced file, remove Hayate's hidden app data from Google Drive settings
  (Manage apps).

### WebDAV

If you choose WebDAV, Hayate sends the same `hayate_sync.json` file to the
server address you enter, using the username and password you provide. These
credentials are stored only on your device. What the server does with the file
is governed by that server's operator.

## Google API data

Hayate's use and transfer of information received from Google APIs adheres to
the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements. Data from Google is used only to
provide the sync feature you turned on. It is not shared with anyone, not used
for advertising, and not read by people.

## Other network requests

To work, Hayate contacts public services such as AniList and Kitsu (anime
information and posters) and YouTube (official episode streams, via yt-dlp).
These requests are made directly from your device to those services, or through
a proxy or Tor if you configure one. Those services have their own privacy
policies.

## Children

Hayate does not knowingly collect personal information from anyone.

## Changes

If this policy changes, the date above will be updated.

## Contact

Questions or requests: open an issue at
https://github.com/Piyush-1927/Hayate-Multi-Platform/issues

# Age-restricted videos

← [Back to README](../README.md)

Some music videos are age-restricted, and YouTube only serves them to a signed-in account.
Without one, the download fails with:

```text
Sign in to confirm your age. This video may be inappropriate for some users.
```

To download these, give chvideo a **cookies file** exported from a signed-in YouTube session:

```bash
chvideo batch links.txt --cookies ~/youtube-cookies.txt
```

`--cookies` works the same way for single songs. To avoid passing it every time, set it once:

```powershell
# Windows (affects terminals opened afterwards)
setx CHVIDEO_COOKIES "$env:USERPROFILE\youtube-cookies.txt"
```

```bash
# Linux
echo "export CHVIDEO_COOKIES=~/youtube-cookies.txt" >> ~/.bashrc
```

Only a **cookies.txt file** is supported. Reading cookies directly out of a browser (yt-dlp's
`--cookies-from-browser`) is intentionally not offered.

## Exporting the cookies file

These steps follow yt-dlp's
[Exporting YouTube cookies](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)
guide. YouTube rotates the cookies of any session that stays open in a browser, which quickly
invalidates an exported file. Exporting from a private window that you then close avoids that.

1. Install a cookies.txt export extension. yt-dlp's
   [FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp) suggests:
   - **Get cookies.txt LOCALLY** for Chrome. Not the similarly named "Get cookies.txt", which was
     removed from the Chrome Web Store as malware.
   - **cookies.txt** for Firefox.

   Allow the extension to run in private/incognito windows.
2. Open a **new private/incognito window** and sign in to YouTube. Make sure it's the only
   private tab open.
3. In that same tab, go to `https://www.youtube.com/robots.txt`.
4. Use the extension to export the **youtube.com** cookies, in Netscape format, to a file.
5. **Close the private window** right away, so that session is never used in the browser again.
6. Move the file somewhere permanent, outside any folder you share or sync, and point
   `--cookies` at it:
   - **Windows:** for example `C:\Users\<you>\youtube-cookies.txt`.
   - **WSL:** copy it into Linux, for example
     `cp /mnt/c/Users/<you>/Downloads/cookies.txt ~/youtube-cookies.txt`.

## What chvideo checks and does

- **Format.** The file must be in Netscape/Mozilla `cookies.txt` format, whose first line is
  `# Netscape HTTP Cookie File` or `# HTTP Cookie File`. A JSON export is rejected with a clear
  message.
- **Line endings.** A file saved by a browser on Windows has Windows (CRLF) line endings. yt-dlp
  on Linux/WSL would then fail with `HTTP Error 400: Bad Request`. chvideo converts the endings
  automatically, so the same file works on Windows and Linux.
- **Sanity check.** At startup it warns if the file has no youtube.com cookies, has no sign-in
  cookies (it was exported while signed out), or has only expired sign-in cookies.
- **Your file is never modified.** yt-dlp writes cookies back to its cookie file when it
  finishes, and batch mode runs several downloads at once. So each download gets its own
  private temporary copy, readable only by you and deleted afterwards.

## Caveats

- **Treat the file like a password.** It grants access to the Google account it came from.
  Keep it out of git; the repository's `.gitignore` already excludes `cookies.txt`.
- **Account risk.** yt-dlp warns that using an account with it can get that account
  temporarily or permanently banned. A throwaway account is the safest choice.
- **Re-export when it stops working.** If downloads fail with a sign-in error even though you
  pass `--cookies`, the session has expired or been rotated. Export a fresh file.
- **"Confirm you're not a bot".** `Sign in to confirm you're not a bot` (or `HTTP Error 429`)
  is YouTube rate-limiting your connection after many requests. It isn't specific to one video.
  Wait a while (often about an hour) and retry. Signed-in requests are challenged less often.

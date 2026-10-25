# Using AstroTranscript

The app runs in a browser at http://127.0.0.1:8787 after you start the server. See the [README](../README.md) for install and data-root setup.

## Projects and recordings

The dashboard lists projects. Inbox is created automatically and holds recordings that are not in another project.

Create a project, then upload audio or import a file that is already on disk. Wav is the usual input. The server also accepts common compressed formats and decodes them with ffmpeg when you process the recording.

The folder id does not change when you rename a recording. The pencil next to a name edits the display title. A title may be up to 200 characters. Clearing it shows the folder id again. The comment under the name holds about 2000 characters. Hover the comment to read it, or click it to edit.

Deleting a recording removes that recording folder. Deleting a project moves its recordings to Inbox if they are still there, so delete recordings first when you want them gone.

## Timeline

The workspace timeline has two lanes: the full file and the speech regions found by voice activity detection. Click the timeline to move the cursor. The space bar starts and pauses playback. The wheel zooms, and dragging pans.

Playback uses one shared session. Leaving the workspace does not stop it. A bar at the bottom shows the file name and play, pause, speed, and volume controls, plus a link back to the recording. Speed runs from 0.5× to 2×. The volume control scales both the original and the cleaned track.

Starting a short preview of a transcript line pauses the main session. Scrubbing a recording that is not the one currently playing does not take over that session.

## Clock

If the filename contains a start time, every transcript time is that start plus the position in the file.

Use “Задать время” when the real start differs from the filename. You set which timeline position matches which clock time. The same correction then shifts the whole recording.

“Копировать смещение” copies the difference between the filename start and the corrected start. “Вставить смещение” adds that difference to the filename start of the recording you paste onto. It does not copy the absolute clock time. A recording whose name has no timestamp cannot take an offset. An offset of zero clears the calibration.

## Markers

The thin strip under the loop ruler is the marker track.

- Click empty space to add a marker.
- Drag a marker to move it.
- Shift-click a marker to name it. Names are up to 80 characters.
- Double-click or right-click a marker to delete it.
- “метка”, or the M key, drops a marker at the playhead.
- “← метка”, “метка →”, Alt+Left, and Alt+Right jump between markers.

A jump moves the cursor. Audio follows only when that recording is already playing. Markers are stored in `recording.json` and survive a reload. A recording can hold 200 markers. Two markers closer than 10 milliseconds are collapsed unless they have different names.

## Listening effects

“Слух / FX” opens the listening panel. These effects change what you hear. They do not replace the stored audio and they do not move the manual sliders on their own.

The chain is:

1. High-pass, shelves, and the graphic EQ.
2. Fixed de-echo comb.
3. Compressor and makeup gain.
4. Speech leveling in the 250–3800 Hz band.
5. Adaptive filter: its own high-pass, eight bands, and low-pass.
6. A limiter.

The adaptive filter tries to keep speech in the 300–4000 Hz range easy to hear and to turn down steady rumble and hiss. Reset turns speech leveling and the adaptive filter off. The “Речь” preset turns them back on. Per-speaker snapshots remember the same settings.

## Speakers

You can assign transcript lines to speakers and keep example clips in a speaker library. A speaker can carry listening settings. While “follow the speaker” is on, the panel switches to that speaker’s settings at the playhead.

## Exports

From the workspace you can download SRT, a plain-text transcript, and an MP3 of the speech regions. Individual lines can be exported as short clips. Loop cuts saved from the workspace are listed with the project.

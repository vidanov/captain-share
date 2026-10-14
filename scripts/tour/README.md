# Recording the tour

The tour at `/tour` (`captain_share/web/tour.*`) replays screenshots of a fictional
setup. Record it again after visible UI changes:

1. `scripts/tour/fixture.sh /tmp/cs-tour` builds a disposable library (a bare Git
   remote with fictional authors), a project with one local skill, and a skill zip.
2. Start the server it prints, with `CAPTAIN_SHARE_HOME` set as shown.
3. `node scripts/tour/capture.mjs "URL#token=…" /tmp/cs-tour captain_share/web/tour`
   drives headless Chrome (macOS path) through first setup, Get, Share, import,
   Update library and Where installed, writes `NN-*.jpg`, and prints the steps.
4. Paste the printed steps into `STEPS` in `tour.js`, and check every screenshot:
   only fictional names and neutral paths may appear.

# Line Tracker - free public website (GitHub Pages)

## Clean setup
1. github.com > your repo `line-tracker` > Settings > scroll to "Danger Zone" > Delete this repository (only if you want a fresh start; otherwise just upload over the old files).
2. New repository: name `line-tracker`, **Public**, **Add README = Off**, Create.
3. Click **uploading an existing file**. Unzip this zip, open the folder, press Ctrl+A, drag everything in, wait for uploads to finish, click **Commit changes**.
4. Check the repo's Code tab. You must see: `docs`, `app.py`, `build_data.py`, `requirements.txt`, `.github`.
   - If `.github` is missing: Add file > Create new file. In the SMALL name box type `.github/workflows/update.yml`.
     Open `UPDATE-WORKFLOW-COPY.txt` from this zip in Notepad, copy everything, paste into the BIG text area, Commit.
   - Open the `docs` folder: you must see `index.html`, `data`, and `.nojekyll`.
     If `.nojekyll` is missing: Add file > Create new file, name `docs/.nojekyll`, type one space in the text area, Commit.
5. Settings > Pages > Source: **Deploy from a branch** > Branch `main`, folder `/docs` > Save.
6. Actions tab > **Update data** > Run workflow > Run workflow. Wait 3-6 minutes for a green check.
7. Open https://YOUR-USERNAME.github.io/line-tracker/

## NBA data
NBA.com often blocks GitHub's servers. If the NBA tab says data is missing or old:
double-click `update-nba-data.bat` on your PC, then in GitHub open `docs/data` > Add file > Upload files > drag in the new `nba.json` > Commit.

## What the tabs do
- Tracker: player, stat, line, Over/Under, 5/10/15/20 games, home/away, head-to-head.
- Upcoming games: only games that have not started; click one to see both rosters. Click a player to open him in the tracker.
- Daily picks: the 20 most consistent players (points, rebounds, assists, 3-pointers) from upcoming games. If too few meet the strict rules, the list is filled with the next best so it is never empty.

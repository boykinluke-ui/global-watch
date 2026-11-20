THE GLOBAL WATCH 1.6.12 — FREE DAILY EDITION

WHAT THIS IS
A browser-based, read-only website with a free scheduled data refresh. After you publish it once, GitHub Actions runs the update job daily without your computer being on. A free public GitHub repository and public website are required for GitHub Pages on GitHub Free.

WHAT THE SCHEDULE DOES
• Daily (about 6:17 a.m. Central Standard Time / 7:17 a.m. Central Daylight Time): checks GDELT and configured public UN Geneva RSS feeds, records the actual results, and builds a dated briefing from stored leads.
• Mondays: also attempts World Bank indicators and UNHCR refugee-statistics data.
• No generic web scraping, login bypass, or fabricated live data. ReliefWeb is off unless you separately configure an approved app name. Joshua Project, ProgressBible and ACLED are not connected in this release.
• A source may fail, rate-limit, or publish older observations. The app records source status and data dates; “daily refresh” means the collector is attempted daily, not that every underlying dataset is new daily.

ONE-TIME SETUP (NO CODING OR POWERSHELL)
1. Install GitHub Desktop (free) and sign in or create a GitHub account.
2. On GitHub.com, create a new PUBLIC repository named global-watch. A public repository is required for free GitHub Pages on GitHub Free. The code and bundled datasets in that repository will be public too.
3. In GitHub Desktop, clone the new repository to your computer. Copy the contents of this project folder into that cloned folder, commit the files, and click “Push origin.”
4. On GitHub.com, open the repository’s Settings → Pages. Under Build and deployment, choose “GitHub Actions.”
5. Open Actions → “Refresh Global Watch data” → “Run workflow.” Wait for both the refresh and Pages deployment to succeed. Open Settings → Pages to get the website address.
6. The scheduled run begins automatically. GitHub can delay scheduled jobs. Public repositories with no repository activity for 60 days may have scheduled workflows disabled; check the Actions page and re-enable/run the workflow if needed.

IMPORTANT DATA-PUBLISHING REVIEW
Publishing to a public repository makes the bundled database and research files public. Before pushing, verify reuse terms for every included dataset. The database includes Pew / Our World in Data-derived religious-composition observations; Pew terms may restrict reproducing an entire or substantial dataset. Obtain permission or remove/replace those records before public release if the applicable terms do not allow redistribution. Check IMB / PeopleGroups.org and Open Doors attribution and reuse terms as well. This package preserves the source labels and limitations, but cannot grant data rights.

WHAT USERS CAN AND CANNOT DO
• Visitors can explore the map, country dossiers, research, prayer and Bible pages.
• Each visitor’s saved countries, notes and prayer journal remain in that visitor’s browser on that device. They do not sync or become shared accounts.
• This static edition has no shared editing, secure multi-user accounts, server-side private journals, or server API for visitor-triggered collection.
• The source data can be inspected in the public repository and the published site’s public data files.

IF SOMETHING LOOKS STALE
Open Actions and inspect the latest “Refresh Global Watch data” run. A green check means the build/deploy completed; inspect the run logs and Source Registry for individual collector results. “Run workflow” triggers an extra refresh. The website is not guaranteed to update at an exact minute, and a successful build does not mean every source returned new records.

NO-KEY SERVICES IN THIS EDITION
GDELT, World Bank API v2, UNHCR Refugee Statistics API and public UN Geneva RSS feeds. The daily briefing summarizes stored leads; it is not a verified intelligence product or a complete country assessment. Official travel, safety, humanitarian and ministry guidance should be checked at the original source before acting.

LOCAL PREVIEW
To preview locally, open web/index.html in a browser. The project’s GitHub Pages workflow is what provides the daily refresh; opening the HTML locally does not run collectors.

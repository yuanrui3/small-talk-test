# Small talk test

A research project on everyday preferences, happiness, and whether a language model can track daily self-reported mood from brief neutral conversation. The project runs on the CPU.

## Research questions and status

**Part 1** tests whether general liking is associated with happiness after accounting for measured life circumstances, and explores short preference quizzes. The 1993 US General Social Survey music result was weak and inconclusive. The Slovak Young People Survey showed a positive combined association, with hobbies and interests carrying the strongest descriptive domain association. These cross-sectional findings do not establish daily mood tracking or causation. See the [Part 1 report](reports/part1.md) and its figures.

**Part 2** is an owner-only, 14-day debugging pilot. A cold reader sees today's neutral chat, while a secondary history reader also sees earlier chats. Neither receives self-ratings. The current protocol and analysis remain fixed until Gate 2 review. Further recruitment awaits the owner's Gate 2 decision. Clinical and diagnostic applications are outside the project's scope.

The [analysis plan](PLAN.md), [decision log](LOG.md), and [Gate 2 research note](reports/gate2-research.md) describe methods, limitations and proposals. The research note's proposed changes have not been applied to the current pilot.

## Public and private material

This repository publishes source code, methods, aggregate Part 1 findings, figures, and synthetic mathematical examples. Raw survey downloads, participant access codes, transcripts, ratings, reader guesses, SQLite databases, API usage records, credentials and the generated owner pilot report remain local.

The plans were committed locally before the relevant outcomes were inspected. The public history preserves the recorded commit sequence and dates while replacing private author and committer identities, which changes commit IDs. Those dates are local provenance, not independent public timestamping or a registry preregistration.

## Setup

The existing environment uses Python 3.12. With Python 3.12 installed, run the following in PowerShell from the repository folder. An existing project environment can skip these setup commands.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Dependencies are pinned in [requirements.txt](requirements.txt). The analysis entry points limit numerical-library threads. No GPU or cluster is required.

## Reproduce Part 1

Obtain the original files and place them in the ignored `data/raw/` folder.

| Filename | Source |
| --- | --- |
| `1993_stata.zip` | [NORC GSS Stata downloads](https://gss.norc.org/get-the-data/stata.html), select 1993 |
| `columns.csv` | [Young People Survey on Kaggle](https://www.kaggle.com/datasets/miroslavsabo/young-people-survey) |
| `responses.csv` | The same Young People Survey download |

Keep the NORC ZIP intact. The loader expects `GSS1993.dta` inside and checks the pinned archive checksum. The checksum transcription correction is recorded in [LOG.md](LOG.md). The Slovak loader reads the supplied column dictionary before the responses.

```powershell
.\.venv\Scripts\python.exe -m part1
```

This regenerates `reports/part1.md` and its figures. Both Slovak CSVs are needed for replication. If either is absent, the command produces a GSS-only report and marks replication pending. The original survey files are not distributed here.

## Reproduce the synthetic mathematics

```powershell
.\.venv\Scripts\python.exe -B scripts/reproduce_gate2_math.py
```

This prints JSON for the Gate 2 numerical examples. It uses seed 41393, reads no participant data, creates no output files and makes no API calls.

## Practice with the local app

```powershell
.\.venv\Scripts\python.exe -m part2 serve
```

Open `http://127.0.0.1:8765/` on the same computer. Practice uses fixed synthetic replies and a temporary SQLite database, which is removed on normal shutdown. Stop the server with Ctrl+C. Running practice does not start a real study session.

## Run the live pilot

Live use requires a Cloudflare account on Workers Free and an appropriate Workers AI API token. The model and request limits are in [part2/config.json](part2/config.json). The implementation is configured for zero paid inference spending on Workers Free; use the account's Free plan and check the provider's current terms before starting a separate study.

Enter the account ID and token into the terminal process. For the token, run its command, wait for the prompt, and then paste the token. Secrets belong outside project files and chat messages.

```powershell
$env:CLOUDFLARE_ACCOUNT_ID = Read-Host 'Cloudflare Account ID'
```

```powershell
$env:CLOUDFLARE_API_TOKEN = [System.Net.NetworkCredential]::new('', (Read-Host 'Paste Workers AI token' -AsSecureString)).Password
```

```powershell
.\.venv\Scripts\python.exe -m part2 serve --live
```

The app binds to loopback at `http://127.0.0.1:8765/`. Keep the terminal open during a session. Live sessions store participant records in `data/pilot/pilot.sqlite3` and send chat text to Cloudflare Workers AI. Ratings, access codes and reader guesses stay local. The configured reader is `@cf/openai/gpt-oss-120b`.

The calendar is fixed from the first started chat. Missed dates remain missing. Guesses are revealed at pilot close. The pilot stops at Gate 2 before any further recruitment. Public source code does not deploy the app or change its local hosting.

After the pilot closes, the following command generates the ignored owner report.

```powershell
.\.venv\Scripts\python.exe -m part2 report
```

To delete a participant's local records, run the following and enter their access code when prompted.

```powershell
.\.venv\Scripts\python.exe -m part2 delete-my-data
```

## Maintain the public history

The original local study history remains on its original branch. A separate `codex/public-release` branch replaces private author and committer identities while preserving trees, parent relationships, messages and dates. Publishing uses only that branch.

The preparation script checks an explicit file allowlist across the entire source history, rejects private-email occurrences in tracked objects and commit messages, and verifies that commits with private identity metadata are unreachable from the public copy. Review changed content for credentials and participant information before each publication. These checks do not identify every possible form of sensitive content.

```powershell
.\.venv\Scripts\python.exe -B scripts/prepare_public_release.py --author-name YOUR_GITHUB_LOGIN --author-email YOUR_GITHUB_NOREPLY_EMAIL
```

The default is a read-only check. After review, add `--write` to recreate the public branch locally. The script never pushes. A separately authorized release uses an explicit refspec, such as `git push origin refs/heads/codex/public-release:refs/heads/main`. Pushing all branches or tags would defeat the separation from the original private metadata.

No code license has been selected for this repository.

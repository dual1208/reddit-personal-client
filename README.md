# Personal Reddit client

Small, non-commercial, single-user command-line client for reading selected r/IELTS threads, preparing a reply for review, and checking replies. It uses Python's standard library. There is no background service or automatic posting.

**Status:** Local code is ready for offline use and tests. Live Reddit API calls are disabled until Reddit explicitly approves this use case. Do not set the approval flag merely because the code or a Reddit account exists. Reddit's [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy) governs access. The [access request form](https://support.reddithelp.com/hc/en-us/requests/new?tf_42139884615700=api_request_type_developer_clone&ticket_form_id=14868593862164) is the first step.

## Offline draft workflow

```bash
python3 reddit_client.py draft t3_POSTID --text 'A proposed reply'
python3 reddit_client.py draft-show DRAFT_ID
```

Use `t1_COMMENTID` when replying to a comment. Omit `--text` to pipe text through standard input. Drafts are stored in `~/.local/share/reddit-personal-client/state.json` with mode 600.

## After Reddit approves access

Follow Reddit's actual approval instructions for account and app registration. Do not assume a human-approved comment can be sent from any account. Register an OAuth app at [Reddit's app settings](https://www.reddit.com/prefs/apps) only if directed or permitted. The code expects an OAuth authorization-code flow and a refresh token; a script client secret is optional in the config. Use a redirect URI that exactly matches your Reddit app settings. The URL can point to a local loopback address; if no local server is listening, copy the complete redirected URL from the browser address bar.

Create two private files:

`~/.config/reddit-personal-client/approval.json`:

```json
{"status": "approved", "reference": "REDDIT_APPROVAL_ID_OR_URL"}
```

`~/.config/reddit-personal-client/config.json`:

```json
{
  "client_id": "YOUR_APPROVED_APP_CLIENT_ID",
  "client_secret": "OPTIONAL_IF_YOUR_APP_TYPE_HAS_ONE",
  "redirect_uri": "http://127.0.0.1:8765/callback",
  "user_agent": "script:personal-ielts-client:v0.1 (by /u/YOUR_USERNAME)"
}
```

Keep both files private (`chmod 700 ~/.config/reddit-personal-client; chmod 600 ~/.config/reddit-personal-client/*.json`). The app stores refresh tokens in `config.json` and never prints them.

```bash
python3 reddit_client.py auth-start
python3 reddit_client.py auth-finish 'FULL_REDIRECT_URL_WITH_CODE_AND_STATE'
python3 reddit_client.py probe
```

The `probe` command performs only the first three read requests: `/api/v1/me`, `/r/IELTS/new`, and `/message/inbox?mark=false`. It reports counts, never stores inbox contents, and does not mark inbox items read.

## Read, review, send, notify

```bash
python3 reddit_client.py new --limit 10
python3 reddit_client.py thread POSTID
python3 reddit_client.py draft t3_POSTID --text 'A proposed reply'
python3 reddit_client.py draft-show DRAFT_ID
python3 reddit_client.py send DRAFT_ID
python3 reddit_client.py inbox
python3 reddit_client.py watch-once
python3 reddit_client.py watch --interval 300
```

`send` shows the sending account, parent ID, and exact draft, then requires typing `SEND DRAFT_ID`. It marks the attempt pending before the POST, preventing an automatic repeat after an uncertain network outcome. A pending attempt must be reconciled manually against Reddit before any retry. `watch-once` records processed inbox IDs locally and prints new items; `watch` repeats every five minutes or longer and raises a local macOS notification when new items appear. Start it yourself after the read-only probe and inbox coverage are verified. It does not run at login or as a service.

The client reads one thread's returned comments but does not expand Reddit `morechildren` markers yet. Inbox coverage may differ from the Reddit app's notification feed and must be verified with an approved account.

## Separate Devvit experiment

The companion `ielts-reply-lab` project was created with Reddit's React Devvit template and runs in a private playtest subreddit. [Devvit's authentication](https://developers.reddit.com/docs/guides/faq) is separate from this local client's OAuth flow. Joining Devvit and running its playtest do not grant Data API access to this Python program; its approval gate remains in force. Devvit is a useful route for community-installed discussion tools, while this local client addresses the personal thread and inbox workflow.

## Data and AI

This repository contains no account data, credentials, or AI API integration. Local drafts and processed inbox IDs remain in private local files. If you use an external AI provider to prepare reply text before passing it to `draft`, disclose that processing to Reddit and review the text before posting. No autonomous replies, votes, DMs, or bulk collection are implemented.

Run local tests with `python3 -m unittest discover -s tests -v`. Tests use fake HTTP responses and make no Reddit API requests.

# rensim-tools

An extension for the Claude desktop app for players of **Renaissance**, the play-by-mail game run
by Constantine Xanthos. Paste in your turn results and the GM's newsletters, ask Claude about your
game in plain English, and plan each turn together. When you're done, Claude gives you the order
codes, already checked, to copy into your email to the GM.

Everything stays on your computer, in a folder you choose. You don't need a terminal or any
other setup.

## Install

1. Install the [Claude desktop app](https://claude.ai/download) (Mac or Windows) and sign in.
2. Download [`rensim.mcpb`](https://github.com/matt-a-t/rensim-tools/releases/latest/download/rensim.mcpb),
   the latest release. To update later, download it again and install it the same way: your game
   folder stays as it is.
3. In Claude, open **Settings → Extensions → Advanced settings → Install Extension…** and choose
   the file. (Double-clicking the file may work too, but not on every computer.)
4. Fill in the form:
   - **Game folder**: an empty folder for your game, for example `Documents\Renaissance`.
   - **Account code**: the first line of every turn result (`ACCOUNT CODE: ABC` → `ABC`).
   - **Family** and **Home town**: distances are measured from your home town.
5. **Turn the extension on** with its switch. It stays off until the form is filled in.

The first time it starts, Claude downloads what the extension needs. That takes a minute or two.

## Each turn

1. **Give Claude your new turn result**, and the newsletter if there is one: paste the text into a
   new chat, or attach the file. Claude saves it in your game folder (without your mailing
   address) and loads it. Say "here's my turn result" and paste it in, and that's all.
2. **Ask anything**:
   - "What changed since last turn?"
   - "Who arrived at my home town?"
   - "Where's the cheapest grain near me?"
   - "What did I order three months ago?"
3. **Plan the turn.** Say "plan my turn", or choose **Plan my turn** from the **+** menu, and say
   what you want: "train as much as possible and send a scout to Edinburgh". Claude asks what it
   needs, checks every order against your latest turn, and replies with:
   - the plan, persona by persona, in plain English,
   - the codes to copy into your email to the GM.

   Claude also saves the orders, so next month it knows what you sent. If you change your mind,
   tell it and it saves the new version.

## Other players' publications

Other players' newsletters, proclamations and letters are propaganda, so Claude keeps what they
say apart from what the GM reports. Each statement becomes a **claim**, with who made it and how
firmly ("it is said", "we deny"). Claude only keeps claims it can quote word for word from the
publication. Paste or attach a publication and ask "what does this claim?". Photos and scans
work too: attach them to the chat.

## Your game folder

```
Renaissance/
  data/raw/turns/            your turn results (and any an ally shares)
  data/raw/newsletters/      the GM's newsletters
  data/raw/player_publications/   other players' publications
  data/claims/               what those publications claim
  orders/                    the orders you sent, one file per month
  inbox/                     drop files here and ask Claude to "file my inbox"
  data/renaissance.duckdb    the database, rebuilt from the files above
```

The files are yours. Back the folder up like any other. If you delete the database, Claude
rebuilds it from the files the next time you give it something new.

**The inbox** takes Word documents, PDFs and text files, which is handy for a pile of old turns
or an ally's compiled city data (`…master_city_data….docx`). Ask Claude to "file my inbox".
Filed files move to `inbox/done/`.

**Spellings.** The same family or place is often spelt differently, and look-alike names can be
different families. Ask Claude to "look for names that might be the same" and it will ask you
about each pair.

## If something isn't working

- **Claude doesn't seem to know about your game.** Check the extension is switched on (Settings →
  Extensions) and start a new chat.
- **You see an error.** Settings → Extensions → **Renaissance turns** shows its logs, and
  **Configure** lets you change the folder or your account details.
- **You used the old version with Claude Code**, through the `renaissance-turn` plugin: remove that
  plugin, since it looks for programs this version doesn't need. Point the extension at your old
  data folder and it picks up everything already there (any `rensim.toml` settings too).

## How it works, and working on it

[docs/architecture.md](docs/architecture.md) covers how it's built and what's in the database.
[docs/development.md](docs/development.md) covers running it from source, testing and releasing.
[docs/glossary.md](docs/glossary.md) explains the game's terms.

MIT licence.

# news-poster

Read README.md first. To install for the user: follow its Install section,
ask the user for the bot token, channel and channel name for `.env` (never
commit `.env`), run the tests and `python3 -m poster.main once --dry`, show
the user the dry-run post, and only then install the service.

Verify changes with `python3 -m unittest discover -s tests -t .`.
`python3 -m poster.main once` (without `--dry`) publishes to the channel.

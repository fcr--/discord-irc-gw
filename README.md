# discord-irc-gw
Discord to IRC gateway!

This application connects as bot to Discord while acting as an IRC Server.

Then, after you connect to this IRC Server using your favourite IRC client, you'll be able to join any discord text channels as traditional IRC channels.

Nevertheless this application is still in an early development stage, so don't expect any kind of interesting feature.

## Setup

    python3 -m pip install discord.py
    git clone https://github.com/fcr--/discord-irc-gw.git discord-irc-gw
    cd discord-irc-gw
    cp config.py.dist config.py
    vi config.py
    ./discord-irc-gw.py

How about `config.py`? The first thing you'll need is the `token` for your bot instance. Follow [this tutorial](https://github.com/reactiflux/discord-irc/wiki/Creating-a-discord-bot-&-getting-a-token) to get that identifier.

Since Discord allows arbitrary strings as nicknames, we have to map them manually. So finally you'll have to specify the mapping for the nicknames. Hint: when you connect to the gateway using your irc client, you'll receive a notification with the identifier and all the info provided by Discord for all the usernames without nick mappings.

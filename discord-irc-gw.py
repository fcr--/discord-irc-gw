#!/usr/bin/python3
# -*- encoding: utf-8 -*-

import config
import discord
import asyncio
import re, shlex, time, multiprocessing

bot = discord.Client()
server_name = 'DiscordIrcGw'

irc_client = None

@bot.async_event
def on_member_join(member):
    print('Member {0.mention} joined.' % str(member))

@bot.async_event
def on_message(message, newmessage=None):
    if message.author.id == bot.user.id:
        return
    n = ([ ircname for ircname, ch in irc_client.joins.items()
        if message.channel.id == ch.id ] + [irc_client.nickname])[0]
    def nickmapper(match):
        if 'u'+match.group(1) in config.nick_mappings:
            return '<' + config.nick_mappings['u'+match.group(1)] + '>'
        return m.group()
    def message_urls(message):
        return { embed.thumbnail.url for embed in message.embeds if embed.thumbnail != embed.Empty }
    if newmessage is not None:
        if newmessage.content != message.content:
            parts = ('(edited) ' + message.content[:10] + ('[..]' if len(message.content)>10 else '') + \
                     ' → ' + newmessage.content).split('\n')
        for url in message_urls(newmessage.embeds) - message_urls(message):
            parts.append('→ URL: ' + url)
    else:
        parts = message.content.split('\n') + ['URL: ' + url for url in message_urls(message)]
    for content in parts:
        content = re.sub(r'<@!?([0-9]{10,})>', nickmapper, content)
        if not message.channel.is_private and n[:1] != '#':
            content = 'From #'+message.channel.name+': ' + content
        irc_client.write_msg(irc_client.member_to_nick(message.author),
                'PRIVMSG', [n, content])

@bot.async_event
def on_message_edit(before, after):
    yield from on_message(before, after)

jukebox_info = {
    'last_url': None,
    'last_message_time': time.time(),
    'last_processes': set(),
    'terminated': False
}

@bot.async_event
def on_member_update(before, after):
    if 'jukebox' in config.mod and config.nick_mappings['u'+after.id] == config.mod['jukebox']['nick']:
        command = """./youtube-play.sh {}""".format(shlex.quote(after.game.url))
        @asyncio.coroutine
        def coro():
            proc = yield from asyncio.create_subprocess_shell(command)
            if jukebox_info['terminated']:
                return proc.terminate()
            jukebox_info['last_processes'].add(proc)
            try:
                yield from proc.wait()
                for p in [ p for p in jukebox_info['last_processes'] if p is not proc ]:
                    try:
                        p.terminate()
                    except:
                        pass
                    jukebox_info['last_processes'].discard(p)
            finally:
                jukebox_info['last_processes'].discard(proc)

        print('notification received for url:', after.game.url)
        if after.game.url is None or after.game.url == '':
            jukebox_info['terminated'] = True
            for p in jukebox_info['last_processes']:
                try:
                    p.terminate()
                except:
                    pass
                jukebox_info['last_processes'].discard(p)
        elif jukebox_info['last_url'] != after.game.url or time.time() - jukebox_info['last_message_time'] > 1:
            jukebox_info['terminated'] = False
            jukebox_info['last_url'] = after.game.url
            jukebox_info['last_message_time'] = time.time()
            asyncio.async(coro())


class IrcServerProtocol(asyncio.Protocol):
    def connection_made(self, transport):
        global irc_client
        irc_client = self
        self.transport = transport
        self.state = 'unconnected'
        # joins[irc_channel_name_in_lowercase] -> channel_instance
        self.joins = {}
        self.line_buffer = []
        self.handlers = {
                'JOIN': self.handle_join,
                'NAMES': self.handle_names,
                'PING': self.handle_ping,
                'PRIVMSG': self.handle_privmsg,
                'USERHOST': self.handle_privmsg,
                'WHO': self.handle_who}

    splitter_re = re.compile(r'([^ :][^ ]*|:.*) *')
    def irc_split(self, text):
        res = [w for w in self.splitter_re.split(text.strip()) if w != '']
        if res[-1][:1] == ':':
            res[-1] = res[-1][1:]
        return res

    def write_smsg(self, cmd, args):
        if any(' ' in a for a in args[:-1]):
            raise Exception('space in non-last command')
        if len(args):
            args[-1] = ':' + args[-1]
        msg = ' '.join([':'+server_name, str(cmd), self.nickname] + args) + '\r\n'
        self.transport.write(msg.encode())

    def write_msg(self, userfrom, cmd, args):
        if any(' ' in a for a in args[:-1]):
            raise Exception('space in non-last command')
        if len(args):
            args[-1] = ':' + args[-1]
        if userfrom[:1] != ':':
            userfrom = ':{0}!{0}@localhost'.format(userfrom)
        msg = ' '.join([userfrom, str(cmd)] + args) + '\r\n'
        self.transport.write(msg.encode())

    def data_received(self, data):
        lines = data.split(b'\n')
        if len(lines) <= 1:
            return self.line_buffer.extend(lines)
        lines[0] = b''.join(self.line_buffer + lines[:1])
        self.line_buffer = lines[-1:]
        for line in lines[:-1]:
            self.line_received(line)

    def line_received(self, data):
        line = self.irc_split(data.decode())
        if len(line) < 1: return
        print('message %r' % line)
        if self.state == 'unconnected':
            if line[0].upper() == 'USER':
                self.username = line[1]
            elif line[0].upper() == 'NICK':
                self.nickname = line[1]
            if hasattr(self, 'username') and hasattr(self, 'nickname'):
                self.state = 'connected'
                self.write_smsg(1, ['Welcome'])
                self.write_smsg(376, ['there was no MOTD.'])
        elif self.state == 'connected':
            if line[0].upper() not in self.handlers:
                self.write_smsg(421, ['Unknown command ' + line[0]])
            else:
                return self.handlers[line[0].upper()](line)

    def handle_join(self, line):
        for ircchannel in line[1].lower().split(','):
            channels = []
            count = 0
            for server in bot.servers:
                for ch in server.channels:
                    if ch.type == discord.ChannelType.text and (ircchannel == '#'+ch.name.lower()
                            or ircchannel == ch.id):
                        channels.append(ch)
            if len(channels) == 0:
                self.write_smsg(403, [ircchannel, 'This channel does not exist in your server!'])
            elif len(channels) > 1:
                self.write_smsg(403, [ircchannel, 'There are more than 1 channel with the same name, use id.'])
                for ch in channels:
                    self.write_smsg('NOTICE', ['*', 'server=%s (%s) -> %s' % (
                        ch.server.name, (ch.topic or '')[:50], ch.id)])
            else:
                self.write_msg(self.nickname, 'JOIN', [ircchannel])
                if channels[0].topic is None:
                    self.write_smsg(331, [ircchannel, 'No topic is set'])
                else:
                    self.write_smsg(332, [ircchannel, channels[0].topic.replace('\n', ' ')])
                self.joins[ircchannel] = channels[0]
                self.handle_names(['NAMES', ircchannel])

    def handle_names(self, line):
        if len(line) < 2:
            return self.write_smsg(366, ['*', 'End of /NAMES list.'])
        if line[1].lower() not in self.joins:
            return self.write_smsg(401, [line[1], 'You are not joined to that channel'])
        ch = self.joins[line[1].lower()]
        nicks = []
        for m in ch.server.members:
            nicks.append(self.member_to_nick(m))
            if 'u'+str(m.id) not in config.nick_mappings:
                self.write_smsg('NOTICE', ['*', ('Missing mapping for \'u{0.id}\' (' +
                    'nick={0.nick}, name={0.name}, discriminator={0.discriminator}, ' +
                    'display_name={0.display_name})').format(m)])
            if len(nicks) > 10:
                self.write_smsg(353, ['=', line[1], ' '.join(nicks)])
                nicks = []
        if len(nicks):
            self.write_smsg(353, ['=', line[1], ' '.join(nicks)])
        self.write_smsg(366, [line[1], 'End of /NAMES list.'])

    def handle_ping(self, line):
        self.write_smsg('PONG', [server_name] + line[1:])

    def handle_privmsg(self, line):
        if len(line)<3:
            return 'love'
        if line[1][:1] == '#': # privmsg to a nick:
            if line[1].lower() not in self.joins:
                return self.write_smsg(401, [line[1], 'Destination channel not joined.'])
            ch = self.joins[line[1].lower()]
        else:
            ch = line[1]
            if ch == '*status':
                return self.handle_status_cmd(line)
            if ch in config.nick_mappings_inv:
                ch = config.nick_mappings_inv[ch]
            if re.match(r'u[0-9]{10,}$', ch):
                ch = int(ch[1:])
            for member in bot.get_all_members():
                if int(member.id) == ch:
                    ch = member
                    break
            else:
                return self.write_smsg(401, [line[1], 'No such nick exists.'])
        nicksre = re.compile(r'\b(' + '|'.join(config.nick_mappings.values()) + r')\b')
        def nick_mapper(match):
            return '<@' + config.nick_mappings_inv[match.group(1)][1:] + '>'
        content = nicksre.sub(nick_mapper, line[2])
        return asyncio.async(bot.send_message(ch, content))

    def handle_status_cmd(self, line):
        words = line[2].split(' ')
        if words[0] == 'youtube':
            @asyncio.coroutine
            def coro():
                voice = yield from bot.join_voice_channel(bot.get_channel(config.mod['youtube']['channel']))
                player = yield from voice.create_ytdl_player(words[1])
                player.start()
            asyncio.async(coro())
        if words[0] == 'eval' and len(words)>1:
            def say(text):
                for line in str(text).split('\n'):
                    self.write_msg('*status', 'PRIVMSG', [self.nickname, line])
            if not hasattr(self, 'eval_locals'):
                self.eval_locals = {'say': say}
            command = line[2].split(' ', 1)[1]
            try:
                exec(command, globals(), self.eval_locals)
            except Exception as ex:
                say('ERR> ' + repr(ex))

    def handle_userhost(self, line):
        if len(line) < 2:
            return self.write_smsg(461, ['Not enough parameters.'])
        msg = ' '.join([ '%s=+%s@127.0.0.1' % (u, self.username)
                for u in line[1:] if u == self.nickname])
        self.write_smsg(302, [msg])

    def translate_mask(self, mask):
        if mask == '0': return re.compile('.*')
        res = []
        for c in mask:
            if c == '*':
                res.append('.*')
            elif c == '?':
                res.append('.')
            else:
                res.append(re.escape(c))
        res.append('$')
        return re.compile(''.join(res))

    def handle_who(self, line):
        if len(line) < 2:
            return self.write_smsg(461, ['Not enough parameters.'])
        if line[1][:1] == '#':
            if line[1].upper() in self.joins:
                for member in self.joins[line[1].upper()].server.members:
                    self.write_smsg(352, [line[1], self.member_to_nick(member),
                        'localhost', server_name, self.member_to_nick(member),
                        'H', ':0', member.display_name])
        else:
            r = self.translate_mask(line[1])
            for member in bot.get_all_members():
                nick = self.member_to_nick(member)
                if r.match(nick):
                    self.write_smsg(352, [line[1], nick, 'localhost',
                        server_name, nick, 'H', ':0', member.display_name])
        self.write_smsg(315, [line[1], 'End of /WHO list.'])

    def member_to_nick(self, member):
        uid = 'u' + str(member.id)
        return config.nick_mappings[uid] if uid in config.nick_mappings else uid

def __main__():
    config.nick_mappings_inv = {}
    for uid, nick in config.nick_mappings.items():
        config.nick_mappings_inv[nick] = uid
    loop = asyncio.get_event_loop()
    coro = loop.create_server(IrcServerProtocol, '127.0.0.1', config.port)
    loop.run_until_complete(coro)

    if hasattr(config, 'email') and hasattr(config, 'password'):
        bot.run(config.email, config.password)
    else:
        bot.run(config.token)

if __name__ == '__main__':
    __main__()

# vi: et sw=4

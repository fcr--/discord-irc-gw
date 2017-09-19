#!/usr/bin/python3
# -*- encoding: utf-8 -*-

import config
import discord
import asyncio
import re, shlex, time, multiprocessing, itertools

bot = discord.Client()
server_name = 'DiscordIrcGw'

irc_client = None
modules = []

class JukeboxModule():
    def __init__(self, cfg):
        self.cfg = cfg
        self.last_url = None
        self.last_message_time = time.time()
        self.last_processes = set()
        self.terminated = False

    def on_ready(self):
        nickid = config.nick_mappings_inv[self.cfg['nick']][1:]
        if 'guild' in self.cfg:
            for member in bot.get_all_members():
                if int(member.id) == int(nickid):
                    yield from bot.send_message(member, '<@%s> guild:%s subscribe' % (nickid, self.cfg['guild']))
                    break
        else:
            for server in bot.servers:
                for ch in server.channels:
                    if ch.type == discord.ChannelType.text and ch.name.lower() == self.cfg['notificationchannel']:
                        chan = ch
            yield from bot.send_message(member, '<@%s>: subscribe' % nickid)

    @asyncio.coroutine
    def on_message(self, msg):
        # vars: after
        if config.nick_mappings['u'+msg.author.id] == self.cfg['nick']:
            @asyncio.coroutine
            def coro(command):
                proc = yield from asyncio.create_subprocess_shell(command)
                for p in self.last_processes:
                    try:
                        p.terminate()
                    except:
                        pass
                    self.last_processes.discard(p)
                self.last_processes.add(proc)
                try:
                    yield from proc.wait()
                    proc.terminate()
                finally:
                    self.last_processes.discard(proc)

            m = re.match(r'(?:^|\n)<@!?%s>.*\nnow playing: *([^\n]+)(?:$|\n)' % bot.user.id, msg.content)
            if m is None:
                return False
            url = m.group(1)
            print('notification received for url:', url)
            # FIXME: add support for pause
            #if after.game is None or after.game.url is None or after.game.url == '':
            #    self.terminated = True
            #    for p in self.last_processes:
            #        try:
            #            p.terminate()
            #        except:
            #            pass
            #        self.last_processes.discard(p)
            if self.last_url != url or not self.is_playing():
                command = """./youtube-play.sh {}""".format(shlex.quote(url))
                self.terminated = False
                self.last_url = url
                self.last_message_time = time.time()
                asyncio.async(coro(command))
            return 'Currently playing' not in msg.content # False for status response

    def is_playing(self):
        return not self.terminated and any(p.returncode is None for p in self.last_processes)


class YoutubeModule():
    def __init__(self, cfg):
        self.cfg = cfg

@bot.async_event
def on_member_join(member):
    print('Member(id={0.id}, display_name={0.display_name}) joined.'.format(member))

@bot.async_event
def on_message(message, newmessage=None):
    if message.author.id == bot.user.id:
        return
    if newmessage is None:
        for m in modules:
            if hasattr(m, 'on_message') and (yield from m.on_message(message)):
                return
    n = ([ ircname for ircname, ch in irc_client.joins.items()
        if message.channel.id == ch.id ] + ['*unjoined'])[0]
    def nickmapper(match):
        if 'u'+match.group(1) in config.nick_mappings:
            return '<' + config.nick_mappings['u'+match.group(1)] + '>'
        return m.group()
    def message_urls(message):
        return { attachment['url'] for attachment in message.attachments if 'url' in attachment }
    for a in message.attachments:
        for k, v in a.items():
            print(k, v)
    parts = message.content.split('\n') + ['URL: ' + url for url in message_urls(message)]
    if newmessage is not None:
        if message.content != newmessage.content:
            parts = ('(edited) ' + message.content[:10] + ('[..]' if len(message.content)>10 else '') + \
                     ' → ' + newmessage.content).split('\n')
        for url in message_urls(newmessage) - message_urls(message):
            parts.append('→ URL: ' + url)
    for content in parts:
        content = re.sub(r'<@!?([0-9]{10,})>', nickmapper, content)
        if not message.channel.is_private and n[:1] != '#':
            content = 'From #'+message.channel.name+': ' + content
        irc_client.write_msg(irc_client.member_to_nick(message.author),
                'PRIVMSG', [n, content])

@bot.async_event
def on_ready():
    for m in modules:
        if hasattr(m, 'on_ready'):
            yield from m.on_ready()

@bot.async_event
def on_message_edit(before, after):
    yield from on_message(before, after)

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
                'LIST': self.handle_list,
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
        if isinstance(cmd, int):
            cmd = '%03d' % cmd
        msg = ' '.join([':'+server_name, cmd, self.nickname] + args) + '\r\n'
        self.transport.write(msg.encode())

    def write_msg(self, userfrom, cmd, args):
        if any(' ' in a for a in args[:-1]):
            raise Exception('space in non-last command')
        if len(args):
            args[-1] = ':' + args[-1]
        if userfrom[:1] != ':':
            userfrom = ':{0}!{0}@localhost'.format(userfrom)
        if isinstance(cmd, int):
            cmd = '%03d' % cmd
        msg = ' '.join([userfrom, cmd] + args) + '\r\n'
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
        for ircchannel, password in itertools.zip_longest(line[1].lower().split(','), line[2].split(',') if len(line) > 2 else []):
            channels = []
            for server in bot.servers:
                for ch in (c for c in server.channels if c.type == discord.ChannelType.text):
                    if (password == ch.id if password and password != 'x' else ircchannel == '#'+ch.name.lower()):
                        channels.append(ch)
            if len(channels) == 0:
                self.write_smsg(403, [ircchannel, 'This channel does not exist in your server!'])
            elif len(channels) > 1:
                self.write_smsg(403, [ircchannel, 'There are more than 1 channel with the same name, use id.'])
                for ch in channels:
                    self.write_smsg('NOTICE', ['*', 'server=%s (%s) -> /join %s %s' % (
                        ch.server.name, (ch.topic or '')[:50], ircchannel, ch.id)])
            else:
                self.write_msg(self.nickname, 'JOIN', [ircchannel])
                if channels[0].topic is None:
                    self.write_smsg(331, [ircchannel, 'No topic is set'])
                else:
                    self.write_smsg(332, [ircchannel, channels[0].topic.replace('\n', ' ')])
                self.joins[ircchannel] = channels[0]
                self.handle_names(['NAMES', ircchannel])

    def handle_list(self, line):
        self.write_smsg(321, ['Channel', 'Id - Server (Topic)'])
        for server in bot.servers:
            for ch in (c for c in server.channels if c.type == discord.ChannelType.text):
                self.write_smsg(322, ['#'+ch.name.lower(), '1', '%s - %s (%s)' % (ch.id , ch.server.name, (ch.topic or '')[:50])])
        self.write_smsg(323, ['End of /LIST'])

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

    urlsplitter_re = re.compile(r'(.*?)((?:https?://[^ ]*)|$)')
    quote_re = re.compile(r'([_*`\\~])')
    def handle_privmsg(self, line):
        if len(line)<3:
            return 'love'
        if line[1][:1] == '#': # privmsg to a channel:
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
        content = self.urlsplitter_re.sub(lambda m: self.quote_re.sub(r'\\\1', m.group(1))+m.group(2), line[2])
        if line[1][:1] == '#': # nick references only on non-private conversations
            nicksre = re.compile(r'\b(' + '|'.join(config.nick_mappings.values()) + r')\b')
            def nick_mapper(match):
                return '<@' + config.nick_mappings_inv[match.group(1)][1:] + '>'
            content = nicksre.sub(nick_mapper, content)
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

    modules.extend(globals()[n.title()+'Module'](c) for n, c in config.mod.items())

    if hasattr(config, 'email') and hasattr(config, 'password'):
        bot.run(config.email, config.password)
    else:
        bot.run(config.token)

if __name__ == '__main__':
    __main__()

# vi: et sw=4

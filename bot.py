import discord
from discord.ext import commands
import sqlite3
import asyncio
import random
import base64
from datetime import datetime

# --- CONFIGURATION ---
# >>> ⚠️⚠️ PASTE YOUR NEW TOKEN HERE ⚠️⚠️ <<<
TOKEN = 'YOUR_SECRET_TOKEN_GOES_INTO_RAILWAY_VARIABLES' 
XP_PER_MESSAGE = 15
XP_COOLDOWN_SECONDS = 10
LEVEL_UP_BASE = 250
LEVEL_UP_MULTIPLIER = 1.5

# --- ANTI-TOXICITY CONFIG ---
# Your list of bad words will trigger the filter
BAD_WORDS = ["Fuck", "fuckass", "wtf", "tf", "the fuck", "fuckass"] 
MUTE_ROLE_NAME = "Muted" 

# --- VERIFICATION/JOIN CONFIG ---
DEFAULT_ROLE_NAME = "Unverified" 
WELCOME_CHANNEL_ID = None 

# --- TICKETING AND REACTION ROLES CONFIG ---
TICKET_CATEGORY_ID = 1447121116320759930 
TICKET_EMOJI = "🎫" 
ROLE_MAPPINGS = { 
    "🎮": "Gamer", 
    "⭐": "Scripter",
    "🎨": "Drawing Ping"
}

# --- DATABASE SETUP ---

conn = sqlite3.connect('levels.db')
c = conn.cursor()

c.execute("""CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 0
)""")
conn.commit()

# --- BOT SETUP ---

intents = discord.Intents.default()
intents.message_content = True 
intents.members = True 

# PREFIX IS SET TO ! (EXCLAMATION MARK)
client = commands.Bot(command_prefix='!', intents=intents, help_command=None) 
xp_cooldowns = {} 

def get_level_xp(level):
    return int(LEVEL_UP_BASE * (level ** LEVEL_UP_MULTIPLIER))

# --- EVENTS ---

@client.event
async def on_ready():
    print(f'Logged in as {client.user} ({client.user.id})')
    print('Bot is ready. Database connected.')

@client.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use that command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        usage = getattr(ctx.command, 'usage', ctx.command.signature)
        # Prefix is now !
        await ctx.send(f"❌ Missing argument. Usage: ! {ctx.command.name} {usage}")
    else:
        print(f"An error occurred: {error}")

@client.event
async def on_member_join(member):
    """Handles auto-assigning default role and sending a welcome message."""
    default_role = discord.utils.get(member.guild.roles, name=DEFAULT_ROLE_NAME)
    if default_role:
        try:
            await member.add_roles(default_role, reason="Auto-assigned unverified role on join.")
        except discord.Forbidden:
            print(f"Error: Bot lacks permission to assign the role '{DEFAULT_ROLE_NAME}'. Check role hierarchy.")

    if WELCOME_CHANNEL_ID:
        welcome_channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
        if welcome_channel:
            embed = discord.Embed(
                title=f"👋 Welcome to {member.guild.name}!",
                description=f"Welcome {member.mention}! Please check the rules channel and get verified.",
                color=discord.Color.green(),
                timestamp=datetime.utcnow()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="Current Members", value=member.guild.member_count, inline=True)
            await welcome_channel.send(embed=embed)


@client.event
async def on_raw_reaction_add(payload):
    # Ignore bot reactions
    if payload.member is None or payload.member.bot:
        return

    # --- REACTION ROLES HANDLING ---
    if payload.emoji.name in ROLE_MAPPINGS:
        role_name_to_assign = ROLE_MAPPINGS.get(payload.emoji.name)
        role = discord.utils.get(payload.member.guild.roles, name=role_name_to_assign)
        
        if role:
            try:
                await payload.member.add_roles(role, reason="Reaction Role Assignment")
            except discord.Forbidden:
                pass

    # --- TICKET CREATION HANDLING ---
    if payload.emoji.name == TICKET_EMOJI and payload.channel_id != TICKET_CATEGORY_ID:
        guild = client.get_guild(payload.guild_id)
        if guild is None or payload.member is None:
            return

        # Check if a ticket for this user already exists
        for channel in guild.channels:
            if channel.name == f"ticket-{payload.member.id}":
                try:
                    channel_obj = client.get_channel(payload.channel_id)
                    message = await channel_obj.fetch_message(payload.message_id)
                    await message.remove_reaction(payload.emoji, payload.member)
                except:
                    pass
                return 

        # Create the ticket channel
        category = guild.get_channel(TICKET_CATEGORY_ID)
        if category is None:
            return
            
        ticket_channel = await guild.create_text_channel(
            f"ticket-{payload.member.id}", 
            category=category,
            topic=f"Ticket created by {payload.member.name} ({payload.member.id})",
            overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                payload.member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
            }
        )
        # Prefix is now !
        await ticket_channel.send(
            f"Hello {payload.member.mention}, a ticket has been opened for you. A staff member will be with you shortly.\n"
            f"Use `!close` to close this ticket."
        )


@client.event
async def on_raw_reaction_remove(payload):
    guild = client.get_guild(payload.guild_id)
    if guild is None:
        return

    # --- REACTION ROLES HANDLING ---
    if payload.emoji.name in ROLE_MAPPINGS:
        member = guild.get_member(payload.user_id)
        role_name_to_remove = ROLE_MAPPINGS.get(payload.emoji.name)
        role = discord.utils.get(guild.roles, name=role_name_to_remove)
        
        if member and role:
            try:
                await member.remove_roles(role, reason="Reaction Role Removal")
            except discord.Forbidden:
                pass


@client.event
async def on_message(message):
    if message.author.bot:
        await client.process_commands(message)
        return
    
    # --- ANTI-TOXICITY (Censor) CHECK ---
    content = message.content.lower()
    
    if any(bad_word.lower() in content for bad_word in BAD_WORDS):
        try:
            await message.delete()
            await message.channel.send(f"⚠️ {message.author.mention}, your message was automatically deleted for containing restricted words.", delete_after=5)
            # Stop here if message was deleted.
            return
        except discord.Forbidden:
            print("Error: Bot lacks permissions to delete messages.")
    
    # --- XP GAIN LOGIC ---
    user_id = message.author.id
    current_time = asyncio.get_event_loop().time()

    default_role = discord.utils.get(message.guild.roles, name=DEFAULT_ROLE_NAME)
    is_unverified = default_role and default_role in message.author.roles
    is_command = message.content.startswith(client.command_prefix)

    # Only award XP if: 
    # 1. User is not unverified
    # 2. Message is not a command
    # 3. User is not on cooldown
    if not is_unverified and not is_command and (user_id not in xp_cooldowns or current_time > xp_cooldowns[user_id]):
        xp_cooldowns[user_id] = current_time + XP_COOLDOWN_SECONDS

        c.execute("SELECT xp, level FROM users WHERE user_id = ?", (user_id,))
        data = c.fetchone()

        if data is None:
            c.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            current_xp = 0
            current_level = 0
        else:
            current_xp, current_level = data
        
        new_xp = current_xp + XP_PER_MESSAGE
        
        xp_needed = get_level_xp(current_level + 1)
        level_up = False
        
        while new_xp >= xp_needed:
            current_level += 1
            new_xp -= xp_needed
            xp_needed = get_level_xp(current_level + 1)
            level_up = True
        
        c.execute("UPDATE users SET xp = ?, level = ? WHERE user_id = ?", (new_xp, current_level, user_id))
        conn.commit()

        if level_up:
            await message.channel.send(
                f"🎉 **LEVEL UP!** {message.author.mention} has reached **Level {current_level}**!"
            )
            
    # CRITICAL LINE: Always process commands at the end.
    await client.process_commands(message)

# --- COMMANDS ---

@client.command(name='help')
async def custom_help(ctx):
    """Displays all available bot commands in categories."""
    
    # Prefix is now !
    embed = discord.Embed(
        title="🤖 KZN Bot Commands",
        description="A list of commands for Krxn.cm/Midnight Chaser. Prefix is `!` (exclamation mark).",
        color=discord.Color.dark_green()
    )
    
    # --- STAFF COMMANDS FIELD (UPDATED PREFIX) ---
    staff_text = (
        "**!addxp**, **!setlevel**, **!resetrank** - Rank management.\n"
        "**!verify** `@user` - Manually verifies a user by removing the unverified role."
    )
    embed.add_field(
        name="🔒 Staff Rank Management",
        value=staff_text,
        inline=False
    )

    # --- MODERATION & TICKETS FIELD (UPDATED PREFIX) ---
    moderation_text = (
        "**!kick**, **!ban**, **!purge**, **!warn** - Standard moderation.\n"
        "**!lock**, **!unlock** - Channel access control.\n"
        "**!mute**, **!unmute** - User communication control.\n"
        "**!pin** - Pins the last message or a replied message.\n"
        "**!slowmode** `[seconds]` - Sets channel slow mode.\n"
        "**!ticketsetup**, **!close** - Ticket management."
    )
    embed.add_field(
        name="🛡️ Moderation & Tickets",
        value=moderation_text,
        inline=False
    )

    # --- SCRIPTING & UTILITY COMMANDS FIELD (UPDATED PREFIX) ---
    scripting_text = (
        "**!code**, **!base64**, **!hire** - Scripting utilities.\n"
        "**!userinfo**, **!serverinfo**, **!id**, **!membercount** - Information lookups.\n"
        "**!rrsetup** - Setup reaction roles message."
    )
    embed.add_field(
        name="🛠️ Scripting & Utility Commands",
        value=scripting_text,
        inline=False
    )
    
    # --- GENERAL & FUN COMMANDS FIELD (UPDATED PREFIX) ---
    general_text = (
        "**!ping**, **!say**, **!avatar**, **!hello** - Standard commands.\n"
        "**!dice**, **!coin**, **!8ball**, **!choose** - Fun commands."
    )
    embed.add_field(
        name="⚙️ General & Fun Commands",
        value=general_text,
        inline=False
    )
    
    # --- LEVELING COMMANDS FIELD (UPDATED PREFIX) ---
    leveling_text = (
        "**!rank** - Shows your current XP, Level, and progress.\n"
        "**!leaderboard** - Displays the top 10 most active members."
    )
    embed.add_field(
        name="📈 Leveling Commands",
        value=leveling_text,
        inline=False
    )

    await ctx.send(embed=embed)

@commands.has_permissions(manage_messages=True)
@client.command(name='pin')
async def pin_message(ctx, message_id: int = None):
    """Pins a message. If replied to, pins the replied message. If given an ID, pins that message. Otherwise, pins the previous message."""
    try:
        if ctx.message.reference and ctx.message.reference.message_id:
            target_message = await ctx.fetch_message(ctx.message.reference.message_id)
        elif message_id:
            target_message = await ctx.fetch_message(message_id)
        else:
            messages = [msg async for msg in ctx.channel.history(limit=2)]
            if len(messages) < 2:
                await ctx.send("❌ Cannot find a message to pin.")
                return
            target_message = messages[1]

        await target_message.pin()
        await ctx.message.delete()
        await ctx.send(f"📌 Message by **{target_message.author.display_name}** has been pinned.", delete_after=5)

    except discord.Forbidden:
        await ctx.send("❌ I do not have permissions to pin messages in this channel.", delete_after=5)
    except discord.NotFound:
        await ctx.send("❌ Message not found.", delete_after=5)
    except Exception as e:
        print(f"Pin error: {e}")
        await ctx.send("❌ An unexpected error occurred while trying to pin the message.", delete_after=5)

@commands.has_permissions(manage_channels=True)
@client.command(name='slowmode', usage='[seconds]')
async def slowmode(ctx, seconds: int):
    """Sets the slow mode delay for the current channel. Set to 0 to disable."""
    if 0 <= seconds <= 21600: 
        await ctx.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            await ctx.send("✅ Slow mode has been disabled in this channel.")
        else:
            await ctx.send(f"✅ Slow mode set to **{seconds} seconds** in this channel.")
    else:
        await ctx.send("❌ Slow mode delay must be between 0 and 21600 seconds (6 hours).")

@commands.has_permissions(kick_members=True)
@client.command(name='verify', usage='@user')
async def verify_user(ctx, member: discord.Member):
    """Staff command to remove the default unverified role from a member."""
    default_role = discord.utils.get(ctx.guild.roles, name=DEFAULT_ROLE_NAME)
    
    if not default_role:
        await ctx.send(f"❌ Error: The unverified role '{DEFAULT_ROLE_NAME}' was not found in the server.")
        return

    if default_role not in member.roles:
        await ctx.send(f"❌ **{member.display_name}** is already verified (does not have the '{DEFAULT_ROLE_NAME}' role).")
        return
        
    try:
        await member.remove_roles(default_role, reason=f"Manually verified by {ctx.author.name}.")
        await ctx.send(f"✅ **{member.display_name}** has been verified and the '{DEFAULT_ROLE_NAME}' role has been removed.")
    except discord.Forbidden:
        await ctx.send("❌ I do not have permission to remove that role. Check my role hierarchy.")

@commands.has_permissions(manage_channels=True)
@client.command(name='addxp', usage='@user [amount]')
async def add_xp(ctx, member: discord.Member, amount: int):
    """Staff command to manually give XP to a member."""
    if amount <= 0:
        await ctx.send("❌ Amount must be positive.")
        return

    c.execute("SELECT xp, level FROM users WHERE user_id = ?", (member.id,))
    data = c.fetchone()
    
    if data is None:
        c.execute("INSERT INTO users (user_id, xp, level) VALUES (?, ?, 0)", (member.id, amount))
        new_xp = amount
        new_level = 0
    else:
        current_xp, current_level = data
        new_xp = current_xp + amount
        new_level = current_level
        
        xp_needed = get_level_xp(new_level + 1)
        level_up = False
        
        while new_xp >= xp_needed:
            new_level += 1
            new_xp -= xp_needed
            xp_needed = get_level_xp(new_level + 1)
            level_up = True

    c.execute("UPDATE users SET xp = ?, level = ? WHERE user_id = ?", (new_xp, new_level, member.id))
    conn.commit()
    
    response = f"✅ Added **{amount} XP** to **{member.display_name}**."
    if level_up:
        response += f" They are now **Level {new_level}**!"
    
    await ctx.send(response)

@commands.has_permissions(administrator=True)
@client.command(name='setlevel', usage='@user [level]')
async def set_level(ctx, member: discord.Member, level: int):
    """Staff command to directly set a member's level."""
    if level < 0:
        await ctx.send("❌ Level cannot be negative.")
        return

    c.execute("SELECT user_id FROM users WHERE user_id = ?", (member.id,))
    if c.fetchone() is None:
        c.execute("INSERT INTO users (user_id, xp, level) VALUES (?, 0, ?)", (member.id, level))
    else:
        c.execute("UPDATE users SET xp = 0, level = ? WHERE user_id = ?", (level, member.id))
    
    conn.commit()
    await ctx.send(f"✅ Set **{member.display_name}** to **Level {level}**.")

@commands.has_permissions(administrator=True)
@client.command(name='resetrank', usage='@user')
async def reset_rank(ctx, member: discord.Member):
    """Staff command to reset a member's XP and Level to 0."""
    c.execute("UPDATE users SET xp = 0, level = 0 WHERE user_id = ?", (member.id,))
    conn.commit()
    await ctx.send(f"✅ Reset rank for **{member.display_name}**. They are now Level 0 with 0 XP.")

@client.command(name='rank')
async def rank(ctx, member: discord.Member = None):
    """Shows a user's current XP and Level."""
    user = member or ctx.author
    c.execute("SELECT xp, level FROM users WHERE user_id = ?", (user.id,))
    data = c.fetchone()

    if data is None:
        await ctx.send(f"**{user.display_name}** is not yet registered. Send a message to earn XP!")
        return

    xp, level = data
    xp_needed = get_level_xp(level + 1)
    
    embed = discord.Embed(
        title=f"📊 {user.display_name}'s Rank",
        color=discord.Color.gold()
    )
    embed.add_field(name="Current Level", value=f"**{level}**", inline=True)
    embed.add_field(name="Current XP", value=f"**{xp}**", inline=True)
    embed.add_field(name="Progress to Next Level", value=f"{xp} / {xp_needed} XP", inline=False)
    
    progress_percent = (xp / xp_needed) * 10
    bar = "█" * int(progress_percent) + "░" * (10 - int(progress_percent))
    embed.set_footer(text=f"Progress: [{bar}]")
    
    await ctx.send(embed=embed)


@client.command(name='leaderboard', aliases=['lb'])
async def leaderboard(ctx):
    """Displays the top 10 most active members by level and XP."""
    c.execute("SELECT user_id, xp, level FROM users ORDER BY level DESC, xp DESC LIMIT 10")
    top_users = c.fetchall()

    if not top_users:
        await ctx.send("The leaderboard is empty. Start chatting to climb the ranks!")
        return

    leaderboard_text = ""
    for index, (user_id, xp, level) in enumerate(top_users, start=1):
        try:
            member = await client.fetch_user(user_id)
            name = member.display_name
        except discord.NotFound:
            name = "Unknown User"
            
        leaderboard_text += f"**{index}.** {name} — Level **{level}** ({xp} XP)\n"

    embed = discord.Embed(
        title="👑 Top 10 Most Active Members",
        description=leaderboard_text,
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

@commands.has_permissions(administrator=True)
@client.command(name='ticketsetup')
async def ticket_setup(ctx):
    """Sets up the reaction message for ticket creation."""
    global TICKET_CATEGORY_ID
    if TICKET_CATEGORY_ID is None:
        await ctx.send("❌ **TICKET_CATEGORY_ID** is not set in the script's configuration. Please update it with the ID of your ticket category.")
        return

    embed = discord.Embed(
        title="🎫 Support Tickets",
        description=f"To open a new support ticket, react with the {TICKET_EMOJI} emoji below.",
        color=discord.Color.red()
    )
    message = await ctx.send(embed=embed)
    await message.add_reaction(TICKET_EMOJI)
    await ctx.message.delete()


@commands.has_permissions(kick_members=True) # Staff permission to close
@client.command(name='close')
async def close_ticket(ctx):
    """Closes the current ticket channel."""
    if not ctx.channel.name.startswith("ticket-"):
        await ctx.send("❌ This is not a ticket channel.")
        return

    await ctx.send("Ticket closing in 5 seconds...")
    await asyncio.sleep(5)
    await ctx.channel.delete()

@commands.has_permissions(administrator=True)
@client.command(name='rrsetup')
async def rr_setup(ctx):
    """Sets up the reaction role message based on ROLE_MAPPINGS."""
    if not ROLE_MAPPINGS:
        await ctx.send("❌ **ROLE_MAPPINGS** is empty in the script configuration. Please add emojis and role names.")
        return

    description = "React below to get the corresponding role:\n\n"
    
    for emoji, role_name in ROLE_MAPPINGS.items():
        description += f"{emoji} - **{role_name}**\n"
        
    embed = discord.Embed(
        title="✨ Select Your Roles",
        description=description,
        color=discord.Color.blue()
    )
    message = await ctx.send(embed=embed)
    
    for emoji in ROLE_MAPPINGS.keys():
        await message.add_reaction(emoji)
    
    await ctx.message.delete()

@client.command(name='code', usage='[language] [snippet]')
async def code_snippet(ctx, lang: str, *, snippet):
    """Formats a code snippet in a specific language using markdown code blocks."""
    await ctx.message.delete()
    code_block = f"```{lang}\n{snippet}\n```"
    embed = discord.Embed(
        title=f"Code Snippet ({lang.upper()})",
        description=code_block,
        color=discord.Color.light_grey()
    )
    embed.set_author(name=f"Shared by {ctx.author.display_name}")
    await ctx.send(embed=embed)

@client.command(name='base64', usage='[encode/decode] [text]')
async def base64_convert(ctx, mode: str, *, text: str):
    """Encodes or decodes text using Base64."""
    await ctx.message.delete()
    
    if mode.lower() == 'encode':
        encoded_bytes = base64.b64encode(text.encode('utf-8'))
        result = encoded_bytes.decode('utf-8')
        title = "Base64 Encoded"
    elif mode.lower() == 'decode':
        try:
            decoded_bytes = base64.b64decode(text)
            result = decoded_bytes.decode('utf-8')
            title = "Base64 Decoded"
        except Exception:
            await ctx.send("❌ Error: Invalid Base64 string for decoding.")
            return
    else:
        # Prefix is now !
        await ctx.send("❌ Usage: `!base64 encode [text]` or `!base64 decode [base64_string]`")
        return
    
    embed = discord.Embed(title=title, color=discord.Color.blue())
    embed.add_field(name="Input", value=f"```\n{text}\n```", inline=False)
    embed.add_field(name="Output", value=f"```\n{result}\n```", inline=False)
    await ctx.send(embed=embed)

@client.command(name='hire', usage='[role] / [details]')
async def hire_post(ctx, role: str, *, details: str):
    """Posts a structured hiring request."""
    if ctx.channel.name != 'hiring' and 'hire' not in ctx.channel.name:
        await ctx.send("🚨 Please use this command in the designated `#hiring` channel or a similar channel.")
        return

    embed = discord.Embed(
        title=f"🚨 HIRING REQUEST: {role.upper()} 🚨",
        description="A member of the community is looking to hire talent.",
        color=discord.Color.red()
    )
    embed.add_field(name="Role/Position", value=f"**{role}**", inline=False)
    embed.add_field(name="Details", value=details, inline=False)
    embed.set_footer(text=f"Contact: {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

    await ctx.message.delete()
    await ctx.send(embed=embed)

@client.command(name='userinfo', aliases=['whois'])
async def user_info(ctx, member: discord.Member = None):
    """Shows detailed information about a user."""
    member = member or ctx.author
    
    embed = discord.Embed(
        title=f"👤 User Information: {member.display_name}",
        color=member.color if member.color != discord.Color.default() else discord.Color.dark_grey(),
        timestamp=datetime.utcnow()
    )
    
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="User ID", value=member.id, inline=False)
    embed.add_field(name="Status", value=str(member.status).title(), inline=True)
    embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d %H:%M UTC"), inline=False)
    
    if member.joined_at:
        embed.add_field(name="Joined Server", value=member.joined_at.strftime("%Y-%m-%d %H:%M UTC"), inline=False)
    
    roles = [role.mention for role in member.roles if role.name != "@everyone"]
    if roles:
        embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles), inline=False)
    
    await ctx.send(embed=embed)

@client.command(name='serverinfo')
async def server_info(ctx):
    """Shows detailed information about the server."""
    guild = ctx.guild
    embed = discord.Embed(
        title=f"🏛️ Server Information: {guild.name}",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    
    embed.set_thumbnail(url=guild.icon.url if guild.icon else discord.Embed.Empty)
    embed.add_field(name="Owner", value=guild.owner.mention, inline=True)
    embed.add_field(name="Server ID", value=guild.id, inline=True)
    embed.add_field(name="Members", value=guild.member_count, inline=True)
    embed.add_field(name="Text Channels", value=len(guild.text_channels), inline=True)
    embed.add_field(name="Voice Channels", value=len(guild.voice_channels), inline=True)
    embed.add_field(name="Roles", value=len(guild.roles), inline=True)
    embed.add_field(name="Created On", value=guild.created_at.strftime("%Y-%m-%d %H:%M UTC"), inline=False)
    
    await ctx.send(embed=embed)

@client.command(name='id', usage='[@user/role/channel]')
async def get_id(ctx, target: discord.abc.Snowflake):
    """Gets the ID of a user, role, or channel."""
    if isinstance(target, discord.Member):
        entity_type = "User"
        name = target.display_name
    elif isinstance(target, discord.Role):
        entity_type = "Role"
        name = target.name
    elif isinstance(target, discord.TextChannel) or isinstance(target, discord.VoiceChannel):
        entity_type = "Channel"
        name = target.name
    else:
        await ctx.send(f"❌ Could not determine the type of entity provided. Try mentioning a user, role, or channel.")
        return

    embed = discord.Embed(
        title=f"🆔 {entity_type} ID Lookup",
        description=f"The ID for **{name}** is:",
        color=discord.Color.dark_gold()
    )
    embed.add_field(name="ID", value=f"`{target.id}`", inline=False)
    await ctx.send(embed=embed)

@client.command(name='membercount', aliases=['mc'])
async def member_count(ctx):
    """Displays the current total member count of the server."""
    await ctx.send(f"👥 The server currently has **{ctx.guild.member_count}** members.")

@client.command(name='choose')
async def choose_option(ctx, *choices):
    """Randomly selects one option from a list of choices."""
    if not choices:
        # Prefix is now !
        await ctx.send("❌ Please provide at least two options for me to choose from. Example: `!choose option1 option2 option3`")
        return
    
    if len(choices) == 1:
        await ctx.send(f"🤔 You gave me only one choice, so the answer is, obviously: **{choices[0]}**.")
    else:
        choice = random.choice(choices)
        await ctx.send(f"✅ I choose: **{choice}**")

@client.command(name='dice')
async def dice_roll(ctx):
    """Rolls a standard six-sided die."""
    roll = random.randint(1, 6)
    await ctx.send(f"🎲 You rolled a **{roll}**!")

@client.command(name='coin')
async def coin_flip(ctx):
    """Flips a coin."""
    result = random.choice(["Heads", "Tails"])
    await ctx.send(f"🪙 The coin landed on **{result}**!")

@client.command(name='8ball', usage='[question]')
async def eight_ball(ctx, *, question: str):
    """Ask the magic 8-ball a question."""
    responses = [
        "It is certain.", "It is decidedly so.", "Without a doubt.", "Yes - definitely.",
        "You may rely on it.", "As I see it, yes.", "Most likely.", "Outlook good.", "Yes.",
        "Signs point to yes.", "Reply hazy, try again.", "Ask again later.",
        "Better not tell you now.", "Cannot predict now.", "Concentrate and ask again.",
        "Don't count on it.", "My reply is no.", "My sources say no.", "Outlook not so good.",
        "Very doubtful."
    ]
    await ctx.send(f"**Question:** {question}\n**🎱 8-Ball Says:** {random.choice(responses)}")

@client.command(name='hello')
async def hello(ctx):
    await ctx.send('Hello there, I am kzn.')

@client.command(name='ping')
async def ping(ctx):
    latency = round(client.latency * 1000)
    await ctx.send(f'Pong! Latency: {latency}ms')

@client.command(name='say', usage='[message]')
async def say(ctx, *, message):
    await ctx.message.delete()
    await ctx.send(message)
    
@client.command(name='avatar', usage='[@user]')
async def avatar(ctx, member: discord.Member = None):
    """Shows a user's avatar in full size."""
    user = member or ctx.author
    embed = discord.Embed(
        title=f"{user.display_name}'s Avatar",
        color=discord.Color.dark_teal()
    )
    embed.set_image(url=user.display_avatar.url)
    await ctx.send(embed=embed)

@commands.has_permissions(kick_members=True)
@client.command(name='warn', usage='@user [reason]')
async def warn_member(ctx, member: discord.Member, *, reason='No reason provided'):
    """Issues a formal warning to a member."""
    try:
        await member.send(
            f"⚠️ You have been **warned** in **{ctx.guild.name}** by {ctx.author.display_name}.\n"
            f"Reason: {reason}"
        )
    except discord.Forbidden:
        pass 
        
    embed = discord.Embed(
        title="⚠️ User Warning Issued",
        color=discord.Color.red()
    )
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    
    await ctx.send(embed=embed)

@commands.has_permissions(manage_channels=True)
@client.command(name='lock')
async def lock_channel(ctx, channel: discord.TextChannel = None):
    """Locks the current channel, preventing @everyone from sending messages."""
    channel = channel or ctx.channel
    overwrite = channel.overwrites_for(ctx.guild.default_role)
    if overwrite.send_messages is False:
        await ctx.send(f"🔒 **{channel.mention}** is already locked.")
        return
        
    overwrite.send_messages = False
    await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send(f"🔒 **{channel.mention}** has been locked.")

@commands.has_permissions(manage_channels=True)
@client.command(name='unlock')
async def unlock_channel(ctx, channel: discord.TextChannel = None):
    """Unlocks the current channel, allowing @everyone to send messages."""
    channel = channel or ctx.channel
    overwrite = channel.overwrites_for(ctx.guild.default_role)
    if overwrite.send_messages is True or overwrite.send_messages is None:
        await ctx.send(f"🔓 **{channel.mention}** is already unlocked.")
        return
        
    overwrite.send_messages = True
    await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send(f"🔓 **{channel.mention}** has been unlocked.")

@commands.has_permissions(kick_members=True)
@client.command(name='mute', usage='@user [duration e.g., 30m, 2h] [reason]')
async def mute_user(ctx, member: discord.Member, duration: str = None, *, reason: str = 'No reason provided'):
    """Mutes a user by applying the 'Muted' role."""
    mute_role = discord.utils.get(ctx.guild.roles, name=MUTE_ROLE_NAME)
    
    if not mute_role:
        await ctx.send(f"❌ Error: The required **'{MUTE_ROLE_NAME}'** role was not found. Please create it manually.")
        return

    if mute_role in member.roles:
        await ctx.send(f"❌ **{member.display_name}** is already muted.")
        return

    await member.add_roles(mute_role, reason=f"Muted by {ctx.author.name}. Reason: {reason}")
    await ctx.send(f"🔇 **{member.display_name}** has been muted. Reason: {reason}")

    if duration:
        time_unit = duration[-1].lower()
        time_value = duration[:-1]
        
        try:
            time_value = int(time_value)
        except ValueError:
            await ctx.send("❌ Invalid duration format. Use formats like `30m` or `2h`.")
            return

        if time_unit == 'm':
            seconds = time_value * 60
        elif time_unit == 'h':
            seconds = time_value * 3600
        elif time_unit == 'd':
            seconds = time_value * 86400
        else:
            await ctx.send("❌ Invalid time unit. Use `m` (minutes), `h` (hours), or `d` (days).")
            return

        await asyncio.sleep(seconds)
        
        if mute_role in member.roles:
            await member.remove_roles(mute_role, reason="Auto-unmute after duration.")
            await ctx.send(f"🔊 **{member.display_name}** has been automatically unmuted.")
        
@commands.has_permissions(kick_members=True)
@client.command(name='unmute', usage='@user [reason]')
async def unmute_user(ctx, member: discord.Member, *, reason: str = 'No reason provided'):
    """Unmutes a user by removing the 'Muted' role."""
    mute_role = discord.utils.get(ctx.guild.roles, name=MUTE_ROLE_NAME)

    if not mute_role:
        await ctx.send(f"❌ Error: The required **'{MUTE_ROLE_NAME}'** role was not found.")
        return
    
    if mute_role not in member.roles:
        await ctx.send(f"❌ **{member.display_name}** is not currently muted.")
        return
        
    await member.remove_roles(mute_role, reason=f"Unmuted by {ctx.author.name}. Reason: {reason}")
    await ctx.send(f"🔊 **{member.display_name}** has been unmuted.")

@commands.has_permissions(kick_members=True)
@client.command(name='kick', usage='@user [reason]')
async def kick(ctx, member: discord.Member, *, reason='No reason provided'):
    """Kicks a member from the server. Requires Kick Members permission."""
    if member.guild_permissions.administrator:
        await ctx.send("❌ I cannot kick an administrator or another high-permission moderator.")
        return
    try:
        await member.kick(reason=reason)
        await ctx.send(f'✅ **{member.display_name}** has been kicked. Reason: {reason}')
    except discord.Forbidden:
        await ctx.send("❌ My role is not high enough to kick this user. Check my permissions.")

@commands.has_permissions(ban_members=True)
@client.command(name='ban', usage='@user [reason]')
async def ban(ctx, member: discord.Member, *, reason='No reason provided'):
    """Bans a member from the server. Requires Ban Members permission."""
    if member.guild_permissions.administrator:
        await ctx.send("❌ I cannot ban an administrator or another high-permission moderator.")
        return
    try:
        await member.ban(reason=reason)
        await ctx.send(f'✅ **{member.display_name}** has been banned. Reason: {reason}')
    except discord.Forbidden:
        await ctx.send("❌ My role is not high enough to ban this user. Check my permissions.")

@commands.has_permissions(manage_messages=True)
@client.command(name='purge', usage='[amount]')
async def purge(ctx, amount: int):
    """Deletes a specified number of messages in the channel."""
    if amount < 1:
        await ctx.send("Please specify a number greater than 0.")
        return
    try:
        deleted = await ctx.channel.purge(limit=amount + 1)
        await ctx.send(f'✅ Deleted {len(deleted) - 1} message(s).', delete_after=5)
    except discord.Forbidden:
        await ctx.send("❌ I don't have the 'Manage Messages' permission.")
    except Exception as e:
        await ctx.send(f"❌ An error occurred during purge: {e}")


# --- CONNECT ---


client.run(TOKEN)

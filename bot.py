import discord
from discord.ext import commands
import random
import json
import os
from dotenv import load_dotenv

# Carrega variáveis do arquivo .env
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')

CHARACTER_FILE = 'characters.json'

# --- Dicionario ---
BUFF_LIBRARY = {
    "benção":           {"tipo": "ataque",      "bonus": 1,  "duracao": 99,  "desc": "+1 Ataque (Cena)"},
    "oração":           {"tipo": "ataque",      "bonus": 2,  "duracao": 99,  "desc": "+2 Ataque/Resist (Cena)"},
    "velocidade":       {"tipo": "ataque",      "bonus": 2,  "duracao": 10,  "desc": "+2 Ataque (10 rodadas)"},
    "foco em arma":     {"tipo": "ataque",      "bonus": 1,  "duracao": 999, "desc": "+1 Ataque (Permanente)"},
    "proteção sagrada": {"tipo": "resistencia", "bonus": 2,  "duracao": 99,  "desc": "+2 Resistência (Cena)"},
    "inspiração":       {"tipo": "ataque",      "bonus": 1,  "duracao": 99,  "desc": "+1 Ataque/Dano (Cena)"},
    "doidos":           {"tipo": "ataque",      "bonus": 1,  "duracao": 4,   "desc": "+1 Ataque (3 rodadas)"},
}

DEBUFF_LIBRARY = {
    "perdição":         {"tipo": "ataque",  "bonus": -1, "duracao": 99, "desc": "-1 Ataque (Cena)"},
    "oração profana":   {"tipo": "ataque",  "bonus": -2, "duracao": 99, "desc": "-2 Ataque/Resist (Cena)"},
    "lentidão":         {"tipo": "ataque",  "bonus": -2, "duracao": 10, "desc": "-2 Ataque (10 rodadas)"},
    "ofuscado":         {"tipo": "ataque",  "bonus": -2, "duracao": 99, "desc": "-2 Ataque e Percepção"},
    "esmorecido":       {"tipo": "mental",  "bonus": -5, "duracao": 99, "desc": "-5 em INT, SAB, CAR e perícias"},
    "agarrado":         {"tipo": "ataque",  "bonus": -2, "duracao": 99, "desc": "Desprevenido, imóvel, -2 Ataque"},
    "caído":            {"tipo": "ataque",  "bonus": -5, "duracao": 99, "desc": "-5 Ataque CC e Defesa CC"},
    "cego":             {"tipo": "pericia", "bonus": -5, "duracao": 99, "desc": "Desprevenido, -5 em FOR e DES"},
    "debilitado":       {"tipo": "pericia", "bonus": -5, "duracao": 99, "desc": "-5 em FOR, DES e CON"},
    "enredado":         {"tipo": "ataque",  "bonus": -2, "duracao": 99, "desc": "Lento, vulnerável, -2 Ataque"},
}

# --- PERSISTÊNCIA ---
def load_data():
    if os.path.exists(CHARACTER_FILE):
        try:
            with open(CHARACTER_FILE, 'r') as f:
                content = json.load(f)
                if "players" not in content:
                    content["players"] = {}
                return content
        except:
            pass
    return {"players": {}, "turn": 0, "active_combat": False, "master_id": None}

def save_data(data_to_save):
    with open(CHARACTER_FILE, 'w') as f:
        json.dump(data_to_save, f, indent=4)

data = load_data()

# --- BOT ---
# Define as permissões do bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'✅ Bot Online: {bot.user}')

@bot.command()
async def sync(ctx):
    bot.tree.copy_global_to(guild=ctx.guild)
    synced = await bot.tree.sync(guild=ctx.guild)
    await ctx.send(f'✅ {len(synced)} comandos sincronizados!')

# --- MESTRE ---

# Função que define um mestre que vai ter permissões para passar rodadas
@bot.tree.command(name='set_mestre', description='Define o mestre')
async def set_mestre(interaction: discord.Interaction):
    data["master_id"] = interaction.user.id
    save_data(data)
    await interaction.response.send_message(f"👑 **{interaction.user.name}** é o Mestre!")

# Função para a pessoa que usou o comanod Set_mestre sair do cargo do mestre
@bot.tree.command(name='sair_mestre', description='Abdica do cargo')
async def sair_mestre(interaction: discord.Interaction):
    if data.get("master_id") != interaction.user.id:
        return await interaction.response.send_message("❌ Você não é o Mestre.", ephemeral=True)
    data["master_id"] = None
    save_data(data)
    await interaction.response.send_message("🏳️ O Mestre saiu.")

# Função para passar a rodada kekw
@bot.tree.command(name='passar_rodada', description='Passa a rodada')
async def proxima(interaction: discord.Interaction):
    if data.get("master_id") != interaction.user.id:
        return await interaction.response.send_message("❌ Apenas o Mestre pode passar rodadas.", ephemeral=True)
    data["turn"] = data.get("turn", 0) + 1
    avisos = []
    for p_id in data["players"]:
        for c_n in data["players"][p_id]:
            if c_n == 'active_character':
                continue
            char = data["players"][p_id][c_n]
            if 'buffs' in char:
                novos = []
                for b in char['buffs']:
                    b['duracao'] -= 1
                    if b['duracao'] > 0:
                        novos.append(b)
                    else:
                        avisos.append(f"⏳ Expirou em **{c_n}**: {b['nome']}")
                char['buffs'] = novos
    save_data(data)
    msg = f"🕒 **RODADA {data['turn']}**"
    if avisos:
        msg += "\n" + "\n".join(avisos)
    await interaction.response.send_message(msg)

# --- JOGADOR ---

# Função para criar personagem de acordo com o UID
@bot.tree.command(name='criarpersonagem', description='Cria um novo personagem')
async def criar(interaction: discord.Interaction, nome: str):
    uid = str(interaction.user.id)
    if uid not in data["players"]:
        data["players"][uid] = {}
    data["players"][uid][nome] = {
        'nome': nome, 'buffs': [],
        'base_ataque': 0, 'base_resistencia': 0,
        'for': 0, 'des': 0, 'con': 0, 'int': 0, 'sab': 0, 'car': 0,
    }
    data["players"][uid]['active_character'] = nome
    save_data(data)
    await interaction.response.send_message(f'✅ **{nome}** criado e definido como personagem ativo!')

# Função para trocar entre os personagens salvos da pessoa que usou o comando 
@bot.tree.command(name='trocarpersonagem', description='Alterna entre seus personagens')
async def switch(interaction: discord.Interaction, nome: str):
    uid = str(interaction.user.id)
    if uid in data["players"] and nome in data["players"][uid]:
        data["players"][uid]['active_character'] = nome
        save_data(data)
        await interaction.response.send_message(f'🔄 Personagem ativo: **{nome}**')
    else:
        await interaction.response.send_message(f'❌ Personagem **{nome}** não encontrado.', ephemeral=True)

# Função Definir os atributos do personagem ativo 
@bot.tree.command(name='definir_atributos', description='Define os atributos do personagem ativo')
async def def_attr(interaction: discord.Interaction, força: int, destreza: int, constituição: int, inteligência: int, sabedoria: int, carisma: int):
    uid = str(interaction.user.id)
    char_n = data.get("players", {}).get(uid, {}).get('active_character')
    if not char_n:
        return await interaction.response.send_message("❌ Nenhum personagem ativo.", ephemeral=True)
    data["players"][uid][char_n].update({
        'for': força, 'des': destreza, 'con': constituição,
        'int': inteligência, 'sab': sabedoria, 'car': carisma,
    })
    save_data(data)
    await interaction.response.send_message(f"✅ Atributos de **{char_n}** atualizados!")

# Função aplicar buffs do dicionario no personagem ativo
@bot.tree.command(name='aplicarbuff', description='Aplica um buff ao personagem ativo')
async def b_app(interaction: discord.Interaction, nome: str):
    uid = str(interaction.user.id)
    char_n = data.get("players", {}).get(uid, {}).get('active_character')
    if not char_n:
        return await interaction.response.send_message("❌ Nenhum personagem ativo.", ephemeral=True)
    lib = BUFF_LIBRARY.get(nome.lower())
    if not lib:
        return await interaction.response.send_message(f"❌ Buff **{nome}** não encontrado.", ephemeral=True)
    data["players"][uid][char_n]['buffs'].append({
        'nome': nome.capitalize(), 'tipo': lib['tipo'],
        'bonus': lib['bonus'], 'duracao': lib['duracao'],
    })
    save_data(data)
    await interaction.response.send_message(f"✨ **{char_n}** recebeu: {lib['desc']}")

# Função para aplicar os debbuffs do dicionario no personagem ativo
@bot.tree.command(name='aplicardebuff', description='Aplica um debuff ao personagem ativo')
async def d_app(interaction: discord.Interaction, nome: str):
    uid = str(interaction.user.id)
    char_n = data.get("players", {}).get(uid, {}).get('active_character')
    if not char_n:
        return await interaction.response.send_message("❌ Nenhum personagem ativo.", ephemeral=True)
    lib = DEBUFF_LIBRARY.get(nome.lower())
    if not lib:
        return await interaction.response.send_message(f"❌ Debuff **{nome}** não encontrado.", ephemeral=True)
    data["players"][uid][char_n]['buffs'].append({
        'nome': nome.capitalize(), 'tipo': lib['tipo'],
        'bonus': lib['bonus'], 'duracao': lib['duracao'],
    })
    save_data(data)
    await interaction.response.send_message(f"💀 **{char_n}** recebeu: {lib['desc']}")

# Função para remover os buffs e debuffs
@bot.tree.command(name='limpar_efeitos', description='Remove todos os buffs/debuffs do personagem ativo')
async def clean(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    char_n = data.get("players", {}).get(uid, {}).get('active_character')
    if not char_n:
        return await interaction.response.send_message("❌ Nenhum personagem ativo.", ephemeral=True)
    data["players"][uid][char_n]['buffs'] = []
    save_data(data)
    await interaction.response.send_message(f"🧹 Efeitos de **{char_n}** limpos!")

# Função para rolar os dados de ataque com os bufss e base_ataque
@bot.tree.command(name='atacar', description='Rola ataque com bônus de buffs')
async def at(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    char_n = data.get("players", {}).get(uid, {}).get('active_character')
    if not char_n:
        return await interaction.response.send_message("❌ Nenhum personagem ativo.", ephemeral=True)
    char = data["players"][uid][char_n]
    dado = random.randint(1, 20)
    bonus = sum(b['bonus'] for b in char['buffs'] if b['tipo'] == 'ataque') + char.get('base_ataque', 0)
    await interaction.response.send_message(f"🎲 **{char['nome']}**: {dado} + {bonus} = **{dado + bonus}**")

# função para rolar os dados de resistencia com os buffs e base_resistencia
@bot.tree.command(name='resistencia', description='Rola resistência com bônus de buffs')
async def res(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    char_n = data.get("players", {}).get(uid, {}).get('active_character')
    if not char_n:
        return await interaction.response.send_message("❌ Nenhum personagem ativo.", ephemeral=True)
    char = data["players"][uid][char_n]
    dado = random.randint(1, 20)
    bonus = sum(b['bonus'] for b in char['buffs'] if b['tipo'] == 'resistencia') + char.get('base_resistencia', 0)
    await interaction.response.send_message(f"🛡️ **{char['nome']}**: {dado} + {bonus} = **{dado + bonus}**")

# Função para salvar os bônus dos personagens base 
@bot.tree.command(name='definir_base', description='Define os Bônus bases de ataque e resistencia.')
async def bon(interaction: discord.Interaction, ataque: int, resistencia: int):
    uid = str(interaction.user.id)
    char_n = data.get("players", {}).get(uid, {}).get('active_character')
    if not char_n:
        return await interaction.response.send_message("❌ Nenhum personagem ativo.", ephemeral=True)
    data["player"][uid][char_n].update({
        'base_ataque': ataque,
        'base_resistencia': resistencia,
    })
    save_data(data)
    await interaction.response.send_message(f"Seus Bônus de Ataque e Resistencia para o {char_n} foram salvos.✅​")

@bot.tree.command(name='status', description='mostra uma review geral do seu personagem')     
async def sta(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    char_n = data.get("players", {}).get(uid, {}).get('active_character')
    if not char_n:
        return await interaction.response.send_message("❌ Nenhum personagem ativo.", ephemeral=True)
    char = data["players"][uid][char_n]
    buffs_ativos = [b['nome'] for b in char['buffs']] or ['nenhum']
    await interaction.response.send_message(
        f"PERSONAGEM: {char_n}\n"
        f"TA base: {char['base_ataque']} | TR base: {char['base_resistencia']}\n"
        f"Efeitos ativos: {', '.join(buffs_ativos)}"
    )
# --- INICIAR ---
if not DISCORD_BOT_TOKEN:
    print("❌ DISCORD_BOT_TOKEN não encontrado! Verifique o arquivo .env")
else:
    bot.run(DISCORD_BOT_TOKEN)

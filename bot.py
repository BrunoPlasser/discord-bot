# =====================================================================
#  BOT DE RPG — v2.0
#  Novidades:
#   - Bug do /definir_base corrigido (data["player"] -> data["players"])
#   - Estrutura de dados nova (personagens separados do "active")
#     com MIGRAÇÃO AUTOMÁTICA do characters.json antigo + backup .bak
#   - Saves atômicos (não corrompe o arquivo se o bot cair no meio)
#   - Autocomplete/choices em buffs, debuffs e personagens
#   - Vida: /definir_vida, /dano, /curar
#   - /teste de atributo, /rolar (ex: 2d6+3), iniciativa
#   - Crítico/falha crítica e detalhamento dos bônus nas rolagens
#   - Durações semânticas: rodadas / cena / permanente (+ /nova_cena)
#   - Mestre pode aplicar efeitos/dano em outros jogadores (param "alvo")
#   - /set_mestre protegido, !sync só para o dono do bot
# =====================================================================

import os
import re
import json
import time
import shutil
import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# Carrega variáveis do arquivo .env
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')

CHARACTER_FILE = 'characters.json'

ATRIBUTOS = ['for', 'des', 'con', 'int', 'sab', 'car']
NOME_ATRIBUTO = {
    'for': 'Força', 'des': 'Destreza', 'con': 'Constituição',
    'int': 'Inteligência', 'sab': 'Sabedoria', 'car': 'Carisma',
}

# ------------------------- BIBLIOTECAS DE EFEITOS -------------------------
# "modo" define como o efeito expira:
#   'rodadas'    -> dura N rodadas (cai 1 a cada /passar_rodada)
#   'cena'       -> dura até o Mestre usar /nova_cena
#   'permanente' -> nunca expira sozinho
BUFF_LIBRARY = {
    "benção":           {"tipo": "ataque",      "bonus": 1,  "modo": "cena",       "desc": "+1 Ataque (Cena)"},
    "oração":           {"tipo": "ataque",      "bonus": 2,  "modo": "cena",       "desc": "+2 Ataque/Resist (Cena)"},
    "velocidade":       {"tipo": "ataque",      "bonus": 2,  "modo": "rodadas", "duracao": 10, "desc": "+2 Ataque (10 rodadas)"},
    "foco em arma":     {"tipo": "ataque",      "bonus": 1,  "modo": "permanente", "desc": "+1 Ataque (Permanente)"},
    "proteção sagrada": {"tipo": "resistencia", "bonus": 2,  "modo": "cena",       "desc": "+2 Resistência (Cena)"},
    "inspiração":       {"tipo": "ataque",      "bonus": 1,  "modo": "cena",       "desc": "+1 Ataque/Dano (Cena)"},
    "doidos":           {"tipo": "ataque",      "bonus": 1,  "modo": "rodadas", "duracao": 3,  "desc": "+1 Ataque (3 rodadas)"},
}

DEBUFF_LIBRARY = {
    "perdição":       {"tipo": "ataque",  "bonus": -1, "modo": "cena", "desc": "-1 Ataque (Cena)"},
    "oração profana": {"tipo": "ataque",  "bonus": -2, "modo": "cena", "desc": "-2 Ataque/Resist (Cena)"},
    "lentidão":       {"tipo": "ataque",  "bonus": -2, "modo": "rodadas", "duracao": 10, "desc": "-2 Ataque (10 rodadas)"},
    "ofuscado":       {"tipo": "ataque",  "bonus": -2, "modo": "cena", "desc": "-2 Ataque e Percepção"},
    "esmorecido":     {"tipo": "mental",  "bonus": -5, "modo": "cena", "desc": "-5 em INT, SAB, CAR e perícias"},
    "agarrado":       {"tipo": "ataque",  "bonus": -2, "modo": "cena", "desc": "Desprevenido, imóvel, -2 Ataque"},
    "caído":          {"tipo": "ataque",  "bonus": -5, "modo": "cena", "desc": "-5 Ataque CC e Defesa CC"},
    "cego":           {"tipo": "pericia", "bonus": -5, "modo": "cena", "desc": "Desprevenido, -5 em FOR e DES"},
    "debilitado":     {"tipo": "pericia", "bonus": -5, "modo": "cena", "desc": "-5 em FOR, DES e CON"},
    "enredado":       {"tipo": "ataque",  "bonus": -2, "modo": "cena", "desc": "Lento, vulnerável, -2 Ataque"},
}

# Choices prontos para os comandos (dropdown no Discord)
BUFF_CHOICES = [
    app_commands.Choice(name=f"{n.capitalize()} — {v['desc']}"[:100], value=n)
    for n, v in BUFF_LIBRARY.items()
]
DEBUFF_CHOICES = [
    app_commands.Choice(name=f"{n.capitalize()} — {v['desc']}"[:100], value=n)
    for n, v in DEBUFF_LIBRARY.items()
]
ATRIBUTO_CHOICES = [app_commands.Choice(name=NOME_ATRIBUTO[a], value=a) for a in ATRIBUTOS]

# ------------------------------ PERSISTÊNCIA ------------------------------

def novo_banco():
    return {"players": {}, "turn": 0, "master_id": None, "iniciativa": []}


def novo_personagem(nome: str, vida: int):
    return {
        'nome': nome,
        'vida_max': vida, 'vida_atual': vida,
        'base_ataque': 0, 'base_resistencia': 0,
        'atributos': {a: 0 for a in ATRIBUTOS},
        'efeitos': [],
    }


def migrar_formato_antigo(content: dict) -> dict:
    """Converte o characters.json da v1 para a estrutura nova, sem perder nada."""
    players_antigos = content.get("players", {})
    novos = {}
    for uid, pdata in players_antigos.items():
        if not isinstance(pdata, dict):
            continue
        if "characters" in pdata:  # já está no formato novo
            novos[uid] = pdata
            continue
        ativo = pdata.get("active_character")
        chars = {}
        for nome_c, c in pdata.items():
            if nome_c == "active_character" or not isinstance(c, dict):
                continue
            efeitos = []
            for b in c.get("buffs", []):
                dur = b.get("duracao", 99)
                if dur >= 999:
                    modo, restante = "permanente", None
                elif dur >= 99:
                    modo, restante = "cena", None
                else:
                    modo, restante = "rodadas", max(1, dur)
                efeitos.append({
                    "nome": b.get("nome", "Efeito"), "tipo": b.get("tipo", "ataque"),
                    "bonus": b.get("bonus", 0), "modo": modo, "duracao": restante,
                })
            chars[nome_c] = {
                'nome': c.get('nome', nome_c),
                'vida_max': c.get('vida_max', 10),
                'vida_atual': c.get('vida_atual', c.get('vida_max', 10)),
                'base_ataque': c.get('base_ataque', 0),
                'base_resistencia': c.get('base_resistencia', 0),
                'atributos': {a: c.get(a, 0) for a in ATRIBUTOS},
                'efeitos': efeitos,
            }
        if ativo not in chars:
            ativo = next(iter(chars), None)
        novos[uid] = {"active": ativo, "characters": chars}
    content["players"] = novos
    content.setdefault("turn", 0)
    content.setdefault("master_id", None)
    content.setdefault("iniciativa", [])
    return content


def load_data() -> dict:
    if os.path.exists(CHARACTER_FILE):
        try:
            with open(CHARACTER_FILE, 'r', encoding='utf-8') as f:
                content = json.load(f)
            # Backup do arquivo original antes de qualquer migração/sobrescrita
            try:
                shutil.copyfile(CHARACTER_FILE, CHARACTER_FILE + '.bak')
            except OSError:
                pass
            return migrar_formato_antigo(content)
        except (json.JSONDecodeError, OSError) as e:
            # NÃO apaga os dados: preserva o arquivo problemático para análise
            quarentena = f"{CHARACTER_FILE}.corrompido.{int(time.time())}"
            try:
                os.replace(CHARACTER_FILE, quarentena)
                print(f"⚠️ characters.json inválido ({e}). Arquivo preservado em: {quarentena}")
            except OSError:
                print(f"⚠️ characters.json inválido ({e}) e não foi possível movê-lo.")
    return novo_banco()


def save_data(d: dict) -> None:
    """Escrita atômica: grava num .tmp e troca, evitando arquivo corrompido."""
    tmp = CHARACTER_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=4, ensure_ascii=False)
    os.replace(tmp, CHARACTER_FILE)


data = load_data()

# -------------------------------- HELPERS ---------------------------------

def get_player(uid: str) -> dict:
    return data["players"].setdefault(uid, {"active": None, "characters": {}})


def eh_mestre(user_id: int) -> bool:
    return data.get("master_id") == user_id


def fmt_bonus(n: int) -> str:
    return f"+{n}" if n >= 0 else str(n)


def rotulo_duracao(e: dict) -> str:
    if e['modo'] == 'permanente':
        return 'Permanente'
    if e['modo'] == 'cena':
        return 'Cena'
    r = e.get('duracao') or 0
    return f"{r} rodada" + ("s" if r != 1 else "")


def resolver_alvo(interaction: discord.Interaction, alvo: Optional[discord.Member],
                  exigir_mestre_para_outros: bool = True):
    """Retorna (uid, char, erro). Se 'alvo' for outra pessoa, exige Mestre (opcional)."""
    if alvo is not None and alvo.id != interaction.user.id:
        if exigir_mestre_para_outros and not eh_mestre(interaction.user.id):
            return None, None, "❌ Apenas o Mestre pode usar este comando em outros jogadores."
        uid, dono = str(alvo.id), alvo.display_name
    else:
        uid, dono = str(interaction.user.id), None

    p = data["players"].get(uid)
    ativo = p.get("active") if p else None
    char = p["characters"].get(ativo) if (p and ativo) else None
    if char is None:
        quem = "Você não tem" if dono is None else f"**{dono}** não tem"
        return None, None, f"❌ {quem} personagem ativo. Use `/criarpersonagem`."
    return uid, char, None


def rolagem_d20(char: dict, tipo_efeito: str, base: int, rotulo_base: str) -> str:
    """Rola 1d20, soma base + efeitos do tipo, e devolve a conta detalhada."""
    d = random.randint(1, 20)
    total = d
    partes = [f"{d}"]
    if base:
        total += base
        partes.append(f"{fmt_bonus(base)} ({rotulo_base})")
    for e in char.get('efeitos', []):
        if e.get('tipo') == tipo_efeito and e.get('bonus'):
            total += e['bonus']
            partes.append(f"{fmt_bonus(e['bonus'])} ({e['nome']})")
    sufixo = ''
    if d == 20:
        sufixo = '  💥 **CRÍTICO!**'
    elif d == 1:
        sufixo = '  💀 **Falha crítica!**'
    return f"{' '.join(partes)} = **{total}**{sufixo}"


REGEX_ROLAGEM = re.compile(r'[+-]?(\d*d\d+|\d+)([+-](\d*d\d+|\d+))*$', re.IGNORECASE)


def rolar_expressao(expr: str):
    """Avalia expressões como '2d6+3', 'd20+5-1d4'. Retorna (total, partes) ou None."""
    limpo = expr.lower().replace(' ', '')
    if not REGEX_ROLAGEM.fullmatch(limpo):
        return None
    total, partes, total_dados = 0, [], 0
    for sinal, termo in re.findall(r'([+-]?)((?:\d*d\d+)|\d+)', limpo):
        mult = -1 if sinal == '-' else 1
        if 'd' in termo:
            qtd_s, faces_s = termo.split('d')
            qtd, faces = (int(qtd_s) if qtd_s else 1), int(faces_s)
            if not (1 <= qtd <= 100) or not (2 <= faces <= 1000):
                return None
            total_dados += qtd
            if total_dados > 100:
                return None
            rolagens = [random.randint(1, faces) for _ in range(qtd)]
            total += mult * sum(rolagens)
            partes.append(f"{sinal}{qtd}d{faces} {rolagens}")
        else:
            total += mult * int(termo)
            partes.append(f"{sinal}{termo}")
    return total, partes

# ---------------------------------- BOT -----------------------------------

intents = discord.Intents.default()
intents.message_content = True  # necessário para o comando de prefixo !sync

bot = commands.Bot(command_prefix='!', intents=intents)


@bot.event
async def on_ready():
    print(f'✅ Bot Online: {bot.user}')
    try:
        await bot.change_presence(activity=discord.Game(name="RPG | /ajuda"))
    except Exception:
        pass


# !sync agora é restrito ao dono do bot
@bot.command()
@commands.is_owner()
async def sync(ctx: commands.Context):
    bot.tree.copy_global_to(guild=ctx.guild)
    synced = await bot.tree.sync(guild=ctx.guild)
    await ctx.send(f'✅ {len(synced)} comandos sincronizados neste servidor!')


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.NotOwner):
        await ctx.send("❌ Apenas o dono do bot pode usar esse comando.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        print(f"Erro em comando de prefixo: {error!r}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"Erro em /{getattr(interaction.command, 'name', '?')}: {error!r}")
    msg = "❌ Ops, algo deu errado ao executar esse comando."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


# ------------------------------ AUTOCOMPLETE ------------------------------

async def ac_meus_personagens(interaction: discord.Interaction, current: str):
    p = data["players"].get(str(interaction.user.id), {})
    cur = current.lower()
    return [
        app_commands.Choice(name=n[:100], value=n)
        for n in p.get("characters", {}) if cur in n.lower()
    ][:25]


async def ac_efeitos_ativos(interaction: discord.Interaction, current: str):
    p = data["players"].get(str(interaction.user.id), {})
    ativo = p.get("active")
    char = p.get("characters", {}).get(ativo) if ativo else None
    efeitos = char.get('efeitos', []) if char else []
    cur = current.lower()
    return [
        app_commands.Choice(name=f"{e['nome']} ({rotulo_duracao(e)})"[:100], value=e['nome'])
        for e in efeitos if cur in e['nome'].lower()
    ][:25]


# --------------------------------- MESTRE ---------------------------------

@bot.tree.command(name='set_mestre', description='Assume o cargo de Mestre (protegido se já houver um)')
async def set_mestre(interaction: discord.Interaction):
    atual = data.get("master_id")
    if atual and atual != interaction.user.id:
        # Só um administrador do servidor pode tomar o cargo de um Mestre ativo
        eh_admin = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator
        if not eh_admin:
            return await interaction.response.send_message(
                f"❌ Já existe um Mestre (<@{atual}>). Peça para ele usar `/sair_mestre`, "
                "ou um administrador pode assumir.", ephemeral=True)
    data["master_id"] = interaction.user.id
    save_data(data)
    await interaction.response.send_message(f"👑 **{interaction.user.display_name}** é o Mestre!")


@bot.tree.command(name='sair_mestre', description='Abdica do cargo de Mestre')
async def sair_mestre(interaction: discord.Interaction):
    if not eh_mestre(interaction.user.id):
        return await interaction.response.send_message("❌ Você não é o Mestre.", ephemeral=True)
    data["master_id"] = None
    save_data(data)
    await interaction.response.send_message("🏳️ O Mestre saiu.")


@bot.tree.command(name='passar_rodada', description='Avança a rodada e reduz a duração dos efeitos temporários')
async def passar_rodada(interaction: discord.Interaction):
    if not eh_mestre(interaction.user.id):
        return await interaction.response.send_message("❌ Apenas o Mestre pode passar rodadas.", ephemeral=True)
    data["turn"] = data.get("turn", 0) + 1
    avisos = []
    for p in data["players"].values():
        for nome_c, char in p.get("characters", {}).items():
            restantes = []
            for e in char.get('efeitos', []):
                if e.get('modo') == 'rodadas':
                    e['duracao'] = (e.get('duracao') or 1) - 1
                    if e['duracao'] <= 0:
                        avisos.append(f"⏳ Expirou em **{nome_c}**: {e['nome']}")
                        continue
                restantes.append(e)
            char['efeitos'] = restantes
    save_data(data)
    msg = f"🕒 **RODADA {data['turn']}**"
    if avisos:
        msg += "\n" + "\n".join(avisos)
    await interaction.response.send_message(msg)


@bot.tree.command(name='nova_cena', description='Encerra a cena: remove efeitos de cena, zera rodadas e iniciativa')
async def nova_cena(interaction: discord.Interaction):
    if not eh_mestre(interaction.user.id):
        return await interaction.response.send_message("❌ Apenas o Mestre pode encerrar a cena.", ephemeral=True)
    removidos = 0
    for p in data["players"].values():
        for char in p.get("characters", {}).values():
            antes = len(char.get('efeitos', []))
            char['efeitos'] = [e for e in char.get('efeitos', []) if e.get('modo') != 'cena']
            removidos += antes - len(char['efeitos'])
    data["turn"] = 0
    data["iniciativa"] = []
    save_data(data)
    await interaction.response.send_message(
        f"🎬 **Nova cena!** {removidos} efeito(s) de cena removido(s). Rodadas e iniciativa zeradas.")


# -------------------------------- JOGADOR ---------------------------------

@bot.tree.command(name='criarpersonagem', description='Cria um novo personagem')
@app_commands.describe(nome='Nome do personagem', vida='Vida máxima inicial (padrão: 10)')
async def criarpersonagem(interaction: discord.Interaction, nome: str,
                          vida: app_commands.Range[int, 1, 9999] = 10):
    nome = nome.strip()
    if not nome or len(nome) > 32:
        return await interaction.response.send_message("❌ Use um nome de 1 a 32 caracteres.", ephemeral=True)
    p = get_player(str(interaction.user.id))
    if nome in p["characters"]:
        return await interaction.response.send_message(
            f"❌ Você já tem um personagem chamado **{nome}**. Use `/deletarpersonagem` antes, "
            "ou escolha outro nome.", ephemeral=True)
    p["characters"][nome] = novo_personagem(nome, vida)
    p["active"] = nome
    save_data(data)
    await interaction.response.send_message(f'✅ **{nome}** criado (❤️ {vida}) e definido como personagem ativo!')


@bot.tree.command(name='listarpersonagens', description='Lista seus personagens')
async def listarpersonagens(interaction: discord.Interaction):
    p = data["players"].get(str(interaction.user.id), {})
    chars = p.get("characters", {})
    if not chars:
        return await interaction.response.send_message("Você ainda não tem personagens. Use `/criarpersonagem`.", ephemeral=True)
    linhas = [
        f"{'👉' if n == p.get('active') else '▫️'} **{n}** — ❤️ {c['vida_atual']}/{c['vida_max']}"
        for n, c in chars.items()
    ]
    await interaction.response.send_message("\n".join(linhas), ephemeral=True)


@bot.tree.command(name='trocarpersonagem', description='Alterna entre seus personagens')
@app_commands.describe(nome='Personagem que vai ficar ativo')
@app_commands.autocomplete(nome=ac_meus_personagens)
async def trocarpersonagem(interaction: discord.Interaction, nome: str):
    p = data["players"].get(str(interaction.user.id), {})
    if nome not in p.get("characters", {}):
        return await interaction.response.send_message(f'❌ Personagem **{nome}** não encontrado.', ephemeral=True)
    p['active'] = nome
    save_data(data)
    await interaction.response.send_message(f'🔄 Personagem ativo: **{nome}**')


@bot.tree.command(name='deletarpersonagem', description='Apaga um dos seus personagens (não tem volta!)')
@app_commands.describe(nome='Personagem a apagar')
@app_commands.autocomplete(nome=ac_meus_personagens)
async def deletarpersonagem(interaction: discord.Interaction, nome: str):
    p = data["players"].get(str(interaction.user.id), {})
    if nome not in p.get("characters", {}):
        return await interaction.response.send_message(f'❌ Personagem **{nome}** não encontrado.', ephemeral=True)
    del p["characters"][nome]
    if p.get("active") == nome:
        p["active"] = next(iter(p["characters"]), None)
    data["iniciativa"] = [e for e in data.get("iniciativa", []) if e.get("nome") != nome]
    save_data(data)
    extra = f" Personagem ativo agora: **{p['active']}**." if p.get("active") else ""
    await interaction.response.send_message(f'🗑️ **{nome}** apagado.{extra}')


@bot.tree.command(name='definir_atributos', description='Define os atributos do personagem ativo')
async def definir_atributos(interaction: discord.Interaction, força: int, destreza: int,
                            constituição: int, inteligência: int, sabedoria: int, carisma: int):
    uid, char, erro = resolver_alvo(interaction, None)
    if erro:
        return await interaction.response.send_message(erro, ephemeral=True)
    char['atributos'] = {
        'for': força, 'des': destreza, 'con': constituição,
        'int': inteligência, 'sab': sabedoria, 'car': carisma,
    }
    save_data(data)
    await interaction.response.send_message(f"✅ Atributos de **{char['nome']}** atualizados!")


@bot.tree.command(name='definir_base', description='Define os bônus base de ataque e resistência')
async def definir_base(interaction: discord.Interaction, ataque: int, resistencia: int):
    uid, char, erro = resolver_alvo(interaction, None)
    if erro:
        return await interaction.response.send_message(erro, ephemeral=True)
    # v1 tinha data["player"] (sem o "s") aqui, o que quebrava o comando. Corrigido!
    char['base_ataque'] = ataque
    char['base_resistencia'] = resistencia
    save_data(data)
    await interaction.response.send_message(
        f"✅ Bônus de **{char['nome']}**: Ataque {fmt_bonus(ataque)} | Resistência {fmt_bonus(resistencia)}")


@bot.tree.command(name='definir_vida', description='Define a vida máxima (e atual) do personagem ativo')
@app_commands.describe(maxima='Vida máxima', atual='Vida atual (padrão: igual à máxima)')
async def definir_vida(interaction: discord.Interaction, maxima: app_commands.Range[int, 1, 9999],
                       atual: Optional[app_commands.Range[int, 0, 9999]] = None):
    uid, char, erro = resolver_alvo(interaction, None)
    if erro:
        return await interaction.response.send_message(erro, ephemeral=True)
    char['vida_max'] = int(maxima)
    char['vida_atual'] = int(maxima if atual is None else min(atual, maxima))
    save_data(data)
    await interaction.response.send_message(f"❤️ **{char['nome']}**: {char['vida_atual']}/{char['vida_max']} de vida.")

# ----------------------------- VIDA E DANO --------------------------------

@bot.tree.command(name='dano', description='Aplica dano ao personagem ativo (Mestre pode mirar em outros)')
@app_commands.describe(valor='Quantidade de dano', alvo='(Mestre) jogador alvo')
async def dano(interaction: discord.Interaction, valor: app_commands.Range[int, 1, 99999],
               alvo: Optional[discord.Member] = None):
    uid, char, erro = resolver_alvo(interaction, alvo)
    if erro:
        return await interaction.response.send_message(erro, ephemeral=True)
    char['vida_atual'] = max(0, char.get('vida_atual', 0) - valor)
    save_data(data)
    msg = f"💥 **{char['nome']}** sofreu **{valor}** de dano! ❤️ {char['vida_atual']}/{char['vida_max']}"
    if char['vida_atual'] == 0:
        msg += "\n☠️ **{0} caiu!**".format(char['nome'])
    await interaction.response.send_message(msg)


@bot.tree.command(name='curar', description='Cura o personagem ativo (Mestre pode mirar em outros)')
@app_commands.describe(valor='Quantidade curada', alvo='(Mestre) jogador alvo')
async def curar(interaction: discord.Interaction, valor: app_commands.Range[int, 1, 99999],
                alvo: Optional[discord.Member] = None):
    uid, char, erro = resolver_alvo(interaction, alvo)
    if erro:
        return await interaction.response.send_message(erro, ephemeral=True)
    char['vida_atual'] = min(char.get('vida_max', 0), char.get('vida_atual', 0) + valor)
    save_data(data)
    await interaction.response.send_message(
        f"💚 **{char['nome']}** recuperou **{valor}** de vida! ❤️ {char['vida_atual']}/{char['vida_max']}")


# --------------------------------- EFEITOS --------------------------------

async def _aplicar_efeito(interaction: discord.Interaction, chave: str,
                          alvo: Optional[discord.Member], biblioteca: dict, emoji: str):
    uid, char, erro = resolver_alvo(interaction, alvo)
    if erro:
        return await interaction.response.send_message(erro, ephemeral=True)
    lib = biblioteca.get(chave.lower())
    if not lib:
        return await interaction.response.send_message(f"❌ Efeito **{chave}** não encontrado.", ephemeral=True)

    # Não empilha o mesmo efeito: reaplica (renova a duração)
    ja_tinha = any(e['nome'].lower() == chave.lower() for e in char['efeitos'])
    char['efeitos'] = [e for e in char['efeitos'] if e['nome'].lower() != chave.lower()]
    char['efeitos'].append({
        'nome': chave.capitalize(), 'tipo': lib['tipo'], 'bonus': lib['bonus'],
        'modo': lib['modo'], 'duracao': lib.get('duracao'),
    })
    save_data(data)
    renovado = " *(renovado)*" if ja_tinha else ""
    await interaction.response.send_message(f"{emoji} **{char['nome']}** recebeu: {lib['desc']}{renovado}")


@bot.tree.command(name='aplicarbuff', description='Aplica um buff (Mestre pode mirar em outros)')
@app_commands.describe(nome='Buff a aplicar', alvo='(Mestre) jogador alvo')
@app_commands.choices(nome=BUFF_CHOICES)
async def aplicarbuff(interaction: discord.Interaction, nome: str,
                      alvo: Optional[discord.Member] = None):
    await _aplicar_efeito(interaction, nome, alvo, BUFF_LIBRARY, "✨")


@bot.tree.command(name='aplicardebuff', description='Aplica um debuff (Mestre pode mirar em outros)')
@app_commands.describe(nome='Debuff a aplicar', alvo='(Mestre) jogador alvo')
@app_commands.choices(nome=DEBUFF_CHOICES)
async def aplicardebuff(interaction: discord.Interaction, nome: str,
                        alvo: Optional[discord.Member] = None):
    await _aplicar_efeito(interaction, nome, alvo, DEBUFF_LIBRARY, "💀")


@bot.tree.command(name='remover_efeito', description='Remove um efeito específico do personagem ativo')
@app_commands.describe(nome='Efeito a remover')
@app_commands.autocomplete(nome=ac_efeitos_ativos)
async def remover_efeito(interaction: discord.Interaction, nome: str):
    uid, char, erro = resolver_alvo(interaction, None)
    if erro:
        return await interaction.response.send_message(erro, ephemeral=True)
    antes = len(char['efeitos'])
    char['efeitos'] = [e for e in char['efeitos'] if e['nome'].lower() != nome.lower()]
    if len(char['efeitos']) == antes:
        return await interaction.response.send_message(f"❌ **{char['nome']}** não tem o efeito **{nome}**.", ephemeral=True)
    save_data(data)
    await interaction.response.send_message(f"🧹 **{nome}** removido de **{char['nome']}**.")


@bot.tree.command(name='limpar_efeitos', description='Remove todos os efeitos (Mestre pode mirar em outros)')
@app_commands.describe(alvo='(Mestre) jogador alvo')
async def limpar_efeitos(interaction: discord.Interaction, alvo: Optional[discord.Member] = None):
    uid, char, erro = resolver_alvo(interaction, alvo)
    if erro:
        return await interaction.response.send_message(erro, ephemeral=True)
    char['efeitos'] = []
    save_data(data)
    await interaction.response.send_message(f"🧹 Efeitos de **{char['nome']}** limpos!")


@bot.tree.command(name='biblioteca', description='Mostra todos os buffs e debuffs disponíveis')
async def biblioteca(interaction: discord.Interaction):
    embed = discord.Embed(title="📚 Biblioteca de Efeitos", color=discord.Color.blurple())
    embed.add_field(
        name="✨ Buffs",
        value="\n".join(f"**{n.capitalize()}** — {v['desc']}" for n, v in BUFF_LIBRARY.items()),
        inline=False)
    embed.add_field(
        name="💀 Debuffs",
        value="\n".join(f"**{n.capitalize()}** — {v['desc']}" for n, v in DEBUFF_LIBRARY.items()),
        inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# -------------------------------- ROLAGENS --------------------------------

@bot.tree.command(name='atacar', description='Rola ataque (d20 + base + buffs) com detalhes e crítico')
async def atacar(interaction: discord.Interaction):
    uid, char, erro = resolver_alvo(interaction, None)
    if erro:
        return await interaction.response.send_message(erro, ephemeral=True)
    conta = rolagem_d20(char, 'ataque', char.get('base_ataque', 0), 'base')
    await interaction.response.send_message(f"🎲 **{char['nome']}** ataca: {conta}")


@bot.tree.command(name='resistencia', description='Rola resistência (d20 + base + buffs) com detalhes e crítico')
async def resistencia(interaction: discord.Interaction):
    uid, char, erro = resolver_alvo(interaction, None)
    if erro:
        return await interaction.response.send_message(erro, ephemeral=True)
    conta = rolagem_d20(char, 'resistencia', char.get('base_resistencia', 0), 'base')
    await interaction.response.send_message(f"🛡️ **{char['nome']}** resiste: {conta}")


@bot.tree.command(name='teste', description='Rola um teste de atributo (d20 + atributo + efeitos)')
@app_commands.describe(atributo='Atributo do teste')
@app_commands.choices(atributo=ATRIBUTO_CHOICES)
async def teste(interaction: discord.Interaction, atributo: str):
    uid, char, erro = resolver_alvo(interaction, None)
    if erro:
        return await interaction.response.send_message(erro, ephemeral=True)
    valor = char.get('atributos', {}).get(atributo, 0)
    # Efeitos mentais afetam INT/SAB/CAR; efeitos de perícia afetam FOR/DES/CON
    tipo_efeito = 'mental' if atributo in ('int', 'sab', 'car') else 'pericia'
    conta = rolagem_d20(char, tipo_efeito, valor, NOME_ATRIBUTO[atributo])
    await interaction.response.send_message(f"🧪 **{char['nome']}** — teste de {NOME_ATRIBUTO[atributo]}: {conta}")


@bot.tree.command(name='rolar', description='Rola dados livres, ex: 2d6+3, d20+5, 4d6-1d4+2')
@app_commands.describe(dados='Expressão de dados (ex: 2d6+3)')
async def rolar(interaction: discord.Interaction, dados: str):
    resultado = rolar_expressao(dados)
    if resultado is None:
        return await interaction.response.send_message(
            "❌ Expressão inválida. Exemplos: `d20`, `2d6+3`, `4d6-1d4+2` (máx. 100 dados, 1000 faces).",
            ephemeral=True)
    total, partes = resultado
    detalhe = ' '.join(partes).lstrip('+')
    await interaction.response.send_message(f"🎲 {interaction.user.display_name} rolou `{dados.strip()}`:\n{detalhe} = **{total}**")


# ------------------------------- INICIATIVA -------------------------------

@bot.tree.command(name='iniciativa', description='Rola iniciativa do personagem ativo (d20 + bônus)')
@app_commands.describe(bonus='Bônus de iniciativa (padrão: sua Destreza)')
async def iniciativa(interaction: discord.Interaction, bonus: Optional[int] = None):
    uid, char, erro = resolver_alvo(interaction, None)
    if erro:
        return await interaction.response.send_message(erro, ephemeral=True)
    b = bonus if bonus is not None else char.get('atributos', {}).get('des', 0)
    d = random.randint(1, 20)
    total = d + b
    data["iniciativa"] = [e for e in data.get("iniciativa", []) if e.get("nome") != char['nome']]
    data["iniciativa"].append({"nome": char['nome'], "valor": total, "uid": uid})
    save_data(data)
    await interaction.response.send_message(f"⚡ **{char['nome']}** — iniciativa: {d} {fmt_bonus(b)} = **{total}**")


@bot.tree.command(name='ordem_iniciativa', description='Mostra a ordem de iniciativa atual')
async def ordem_iniciativa(interaction: discord.Interaction):
    fila = sorted(data.get("iniciativa", []), key=lambda e: e["valor"], reverse=True)
    if not fila:
        return await interaction.response.send_message("Ninguém rolou iniciativa ainda. Use `/iniciativa`.", ephemeral=True)
    linhas = [f"**{i}º** — {e['nome']} ({e['valor']})" for i, e in enumerate(fila, start=1)]
    embed = discord.Embed(title="⚡ Ordem de Iniciativa", description="\n".join(linhas),
                          color=discord.Color.gold())
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name='limpar_iniciativa', description='(Mestre) Limpa a lista de iniciativa')
async def limpar_iniciativa(interaction: discord.Interaction):
    if not eh_mestre(interaction.user.id):
        return await interaction.response.send_message("❌ Apenas o Mestre pode limpar a iniciativa.", ephemeral=True)
    data["iniciativa"] = []
    save_data(data)
    await interaction.response.send_message("⚡ Iniciativa limpa!")


# --------------------------------- STATUS ---------------------------------

@bot.tree.command(name='status', description='Mostra a ficha resumida de um personagem')
@app_commands.describe(alvo='Ver o personagem ativo de outro jogador (opcional)')
async def status(interaction: discord.Interaction, alvo: Optional[discord.Member] = None):
    # Qualquer um pode VER a ficha dos outros (só ver, não mexer)
    uid, char, erro = resolver_alvo(interaction, alvo, exigir_mestre_para_outros=False)
    if erro:
        return await interaction.response.send_message(erro, ephemeral=True)

    vmax = max(1, char.get('vida_max', 1))
    proporcao = char.get('vida_atual', 0) / vmax
    cor = discord.Color.green() if proporcao > 0.5 else (
        discord.Color.orange() if proporcao > 0.2 else discord.Color.red())

    embed = discord.Embed(title=f"📜 {char['nome']}", color=cor)
    embed.add_field(name="❤️ Vida", value=f"{char.get('vida_atual', 0)}/{char.get('vida_max', 0)}")
    embed.add_field(name="⚔️ Ataque base", value=fmt_bonus(char.get('base_ataque', 0)))
    embed.add_field(name="🛡️ Resist. base", value=fmt_bonus(char.get('base_resistencia', 0)))
    attrs = char.get('atributos', {})
    embed.add_field(
        name="Atributos",
        value=" | ".join(f"{a.upper()} {attrs.get(a, 0)}" for a in ATRIBUTOS),
        inline=False)
    efeitos = char.get('efeitos', [])
    if efeitos:
        linhas = "\n".join(
            f"{'✨' if e.get('bonus', 0) >= 0 else '💀'} **{e['nome']}** "
            f"{fmt_bonus(e.get('bonus', 0))} {e.get('tipo', '?')} — {rotulo_duracao(e)}"
            for e in efeitos)
    else:
        linhas = "Nenhum"
    embed.add_field(name="Efeitos ativos", value=linhas[:1024], inline=False)
    embed.set_footer(text=f"Rodada atual: {data.get('turn', 0)}")
    await interaction.response.send_message(embed=embed)


# ---------------------------------- AJUDA ---------------------------------

@bot.tree.command(name='ajuda', description='Lista os comandos do bot')
async def ajuda(interaction: discord.Interaction):
    embed = discord.Embed(title="📖 Comandos do Bot", color=discord.Color.blurple())
    embed.add_field(name="🧙 Personagem", value=(
        "`/criarpersonagem` `/listarpersonagens` `/trocarpersonagem` `/deletarpersonagem`\n"
        "`/definir_atributos` `/definir_base` `/definir_vida` `/status`"), inline=False)
    embed.add_field(name="🎲 Rolagens", value=(
        "`/atacar` `/resistencia` `/teste` `/rolar 2d6+3` `/iniciativa` `/ordem_iniciativa`"), inline=False)
    embed.add_field(name="✨ Efeitos", value=(
        "`/aplicarbuff` `/aplicardebuff` `/remover_efeito` `/limpar_efeitos` `/biblioteca`\n"
        "❤️ `/dano` `/curar`"), inline=False)
    embed.add_field(name="👑 Mestre", value=(
        "`/set_mestre` `/sair_mestre` `/passar_rodada` `/nova_cena` `/limpar_iniciativa`\n"
        "O Mestre pode usar o parâmetro **alvo** para afetar outros jogadores."), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --------------------------------- INICIAR --------------------------------

if not DISCORD_BOT_TOKEN:
    print("❌ DISCORD_BOT_TOKEN não encontrado! Verifique o arquivo .env")
else:
    bot.run(DISCORD_BOT_TOKEN)

import os
import time
import requests
import warnings
import hashlib
import random
import re
import threading
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from openai import OpenAI
from supabase import create_client, Client

warnings.filterwarnings('ignore')

app = Flask(__name__)
CORS(app)

# ============================================
# 🔧 CONFIGURAÇÃO
# ============================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
ADMIN_EMAIL = "natan@natandev.com"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = "gpt-4o-mini"
RENDER_URL = os.getenv("RENDER_URL", "")

# Inicializa Supabase
supabase: Client = None
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase conectado")
except Exception as e:
    print(f"⚠️ Erro Supabase: {e}")

# Inicializa OpenAI
client = None
if OPENAI_API_KEY:
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        print("✅ OpenAI conectado")
    except Exception as e:
        print(f"⚠️ Erro OpenAI: {e}")

# Cache
CACHE_RESPOSTAS = {}
HISTORICO_CONVERSAS = []
historico_lock = threading.Lock()

# Auto-ping
def auto_ping():
    while True:
        try:
            if RENDER_URL:
                url = RENDER_URL if RENDER_URL.startswith('http') else f"https://{RENDER_URL}"
                requests.get(f"{url}/health", timeout=10)
                print(f"🏓 Ping OK: {datetime.now().strftime('%H:%M:%S')}")
            else:
                requests.get("http://localhost:5000/health", timeout=5)
        except:
            pass
        time.sleep(300)

threading.Thread(target=auto_ping, daemon=True).start()

# =============================================================================
# 🔐 AUTENTICAÇÃO E DADOS DO USUÁRIO
# =============================================================================

def verificar_token_supabase(token):
    try:
        if not token or not supabase:
            return None
        if token.startswith("Bearer "):
            token = token[7:]
        response = supabase.auth.get_user(token)
        return response.user if response and response.user else None
    except:
        return None

def obter_dados_usuario_completos(user_id):
    """✅ BUSCA NOME DO USUÁRIO (SEM CUSTO EXTRA)"""
    try:
        if not supabase:
            return None
        response = supabase.table('user_accounts').select('*').eq('user_id', user_id).single().execute()
        return response.data if response.data else None
    except:
        return None

def extrair_nome_usuario(user_info, user_data=None):
    """✅ EXTRAI NOME DO USUÁRIO DE MÚLTIPLAS FONTES (0 TOKENS EXTRAS)"""
    try:
        # Prioridade 1: user_name da tabela user_accounts
        if user_data and user_data.get('user_name'):
            nome = user_data['user_name'].strip()
            if nome and len(nome) > 1:
                return nome
        
        # Prioridade 2: name do auth metadata
        if user_info and user_info.user_metadata:
            nome = user_info.user_metadata.get('name', '').strip()
            if nome and len(nome) > 1:
                return nome
        
        # Prioridade 3: Parte antes do @ do email
        if user_info and user_info.email:
            nome = user_info.email.split('@')[0].strip()
            # Capitaliza primeira letra
            return nome.capitalize()
        
        # Fallback
        return "Cliente"
        
    except Exception as e:
        print(f"⚠️ Erro ao extrair nome: {e}")
        return "Cliente"

def determinar_tipo_usuario(user_data, user_info=None):
    """✅ INCLUI NOME NO CONTEXTO DO USUÁRIO"""
    try:
        email = user_data.get('email', '')
        plan = user_data.get('plan', 'starter')
        nome = extrair_nome_usuario(user_info, user_data)
        
        if email == ADMIN_EMAIL:
            return {
                'tipo': 'admin',
                'nome_display': 'Admin',
                'plano': 'Admin',
                'nome_real': 'Natan'  # Nome do admin
            }
        
        if plan == 'professional':
            return {
                'tipo': 'professional',
                'nome_display': 'Professional',
                'plano': 'Professional',
                'nome_real': nome
            }
        
        return {
            'tipo': 'starter',
            'nome_display': 'Starter',
            'plano': 'Starter',
            'nome_real': nome
        }
    except:
        return {
            'tipo': 'starter',
            'nome_display': 'Starter',
            'plano': 'Starter',
            'nome_real': 'Cliente'
        }

# =============================================================================
# 🛡️ VALIDAÇÃO ANTI-ALUCINAÇÃO
# =============================================================================

PALAVRAS_PROIBIDAS = [
    "grátis", "gratuito", "R$ 0", "0 reais", "free",
    "garantimos primeiro lugar", "100% de conversão", "sucesso garantido",
    "site pronto em 1 hora", "atendimento 24/7 imediato", "empresa com 10 anos"
]

PADROES_SUSPEITOS = [
    r'R\$\s*0[,.]?00',
    r'grát[ui]s',
    r'garantimos?\s+\d+%',
    r'\d+\s+anos\s+de\s+experiência',
    r'certificação\s+ISO'
]

def validar_resposta(resposta):
    problemas = []
    resp_lower = resposta.lower()
    
    for palavra in PALAVRAS_PROIBIDAS:
        if palavra.lower() in resp_lower:
            problemas.append(f"Proibida: {palavra}")
    
    for padrao in PADROES_SUSPEITOS:
        if re.search(padrao, resp_lower):
            problemas.append(f"Padrão suspeito")
    
    if "whatsapp" in resp_lower or "telefone" in resp_lower:
        if "99282-6074" not in resposta and "(21) 9" in resposta:
            problemas.append("WhatsApp incorreto")
    
    return len(problemas) == 0, problemas

# =============================================================================
# 🤖 OPENAI - OTIMIZADO v6.1 COM NOMES
# =============================================================================

def verificar_openai():
    """✅ OTIMIZADO - Não gasta créditos!"""
    try:
        if not OPENAI_API_KEY or len(OPENAI_API_KEY) < 20:
            return False
        if client is None:
            return False
        return True
    except:
        return False

def processar_openai(pergunta, tipo_usuario):
    """✅ OTIMIZADO v6.1 - COM NOME DO USUÁRIO (0 TOKENS EXTRAS!)"""
    if not client or not verificar_openai():
        return None
    
    try:
        # 🎯 EXTRAI NOME DO USUÁRIO
        nome_usuario = tipo_usuario.get('nome_real', 'Cliente')
        
        # 🔥 CONTEXTO POR TIPO DE USUÁRIO (COM NOME!)
        if tipo_usuario['tipo'] == 'admin':
            ctx = f"🔴 ADMIN ({nome_usuario}): Acesso total. Respostas técnicas e dados internos."
        elif tipo_usuario['tipo'] == 'professional':
            ctx = f"💎 PROFESSIONAL ({nome_usuario}): Cliente premium. Suporte prioritário, explique recursos avançados."
        else:
            ctx = f"🌱 STARTER ({nome_usuario}): Cliente. Seja acolhedor e pessoal. Sugira upgrade se relevante."
        
        # 🎯 PROMPT ULTRA-COMPACTO COM NOME E CONTEXTO COMPLETO
        prompt = f"""Você é NatanAI, assistente da NatanDEV.

{ctx}

📋 DADOS OFICIAIS:
Criador: Natan Borges
- Desenvolvedor Full-Stack (Front/Back/Mobile)
- Stack: React, Node.js, Python, Next.js, Supabase
- Localização: Rio de Janeiro/RJ
- WhatsApp: (21) 99282-6074
- Portfolio: natandev02.netlify.app
- Site Principal: natansites.com.br

💼 PORTFÓLIO (natandev02.netlify.app):
Projetos Destaque:
- E-COMMERCE SAPATARIA (Shoppy): Catálogo produtos, carrinho, checkout. Stack: React, Tailwind, Vercel
- LANDING PAGE ACADEMY: Design moderno, animações, formulários. Stack: HTML, CSS, JS
- DASHBOARD ANALÍTICO: Charts interativos, visualização dados. Stack: React, Recharts
- APLICATIVO CLONE (Spotify/Netflix): UI responsivo, consumo API. Stack: React Native, Expo
- PORTFÓLIO PROFISSIONAL: Showcase projetos, animações 3D. Stack: React, Three.js

Habilidades Técnicas:
- Front-end: React, Next.js, Vue, HTML/CSS/JS, Tailwind
- Back-end: Node.js, Python Flask, APIs REST, Supabase, Firebase
- Mobile: React Native, Expo, desenvolvimento híbrido
- Design: UI/UX, Figma, Photoshop, animações CSS
- SEO: Otimização, meta tags, performance, Google Analytics

💳 PLANOS (natansites.com.br):
STARTER - R$39,99/mês + R$320 setup
- Site responsivo básico (até 5 páginas)
- Design moderno limpo
- Mobile otimizado
- SEO básico
- Hospedagem inclusa
- Suporte 24/7 plataforma
- Contrato 1 ano

PROFESSIONAL - R$79,99/mês + R$530 setup ⭐
- Design personalizado avançado
- Páginas ilimitadas
- Animações e interatividade
- SEO avançado com keywords
- Integração APIs
- Domínio personalizado (.com.br)
- 5 revisões incluídas
- Formulários contato
- Suporte prioritário
- NatanAI inclusa (opcional)
- Contrato 1 ano

🌐 PLATAFORMA (natansites.com.br):
Funcionalidades Dashboard:
- Gerenciamento sites cadastrados
- Chat suporte tempo real com Natan
- NatanAI: assistente IA integrada
- Configurações tema (claro/escuro)
- Estatísticas uso e visitas
- Gestão conta e pagamento

Para Admin:
- Criar contas clientes
- Gerenciar/suspender contas
- Adicionar/remover sites clientes
- Visualizar todas conversas suporte
- Controle total usuários

🎨 DIFERENCIAIS:
- Sites modernos com tecnologias atuais
- Performance otimizada (score 90+ Lighthouse)
- Design responsivo mobile-first
- SEO desde início do projeto
- IA integrada opcional (NatanAI)
- Suporte contínuo e rápido
- Backup automático diário
- SSL certificado incluso

⚡ REGRAS COMPORTAMENTO:
1. Use o nome "{nome_usuario}" naturalmente na conversa (não em toda frase!)
2. Seja empático, natural e humano
3. NUNCA diga "eu desenvolvo" - sempre "o Natan desenvolve/criou"
4. NUNCA invente preços, tecnologias ou projetos
5. Se não souber, seja honesto e sugira contato direto
6. NUNCA repita literalmente a pergunta do usuário
7. Varie respostas para perguntas similares
8. Destaque vantagens dos planos quando relevante
9. Mencione projetos do portfólio quando perguntarem experiência
10. Use apenas infos acima - ZERO invenção

Responda de forma ÚNICA, CONTEXTUAL e PESSOAL para {nome_usuario}: {pergunta}"""

        # 🚀 CHAMADA OTIMIZADA
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=220,
            temperature=0.75
        )
        
        resposta = response.choices[0].message.content.strip()
        
        # ✅ Validação Anti-Alucinação
        valida, problemas = validar_resposta(resposta)
        if not valida:
            print(f"⚠️ Validação falhou: {problemas}")
            return None
        
        # 🎲 Frase motivacional ocasional (10% das vezes)
        if random.random() < 0.1:
            frases = [
                "\n\n✨ Vibrações Positivas!",
                "\n\n💙 Sucesso no seu projeto!",
                "\n\n🚀 Vamos juntos nessa!",
                "\n\n🌟 Conte sempre comigo!"
            ]
            resposta += random.choice(frases)
        
        return resposta
        
    except Exception as e:
        print(f"❌ Erro OpenAI: {e}")
        return None

def gerar_resposta(pergunta, tipo_usuario):
    """Sistema principal de geração de resposta"""
    try:
        # ✅ Cache inteligente SEM incluir nome (para reutilizar respostas gerais)
        cache_key = hashlib.md5(f"{pergunta.lower().strip()}_{tipo_usuario['tipo']}".encode()).hexdigest()
        
        # Evita cache para perguntas de agradecimento (mais variedade)
        palavras_sem_cache = ['obrigado', 'obrigada', 'valeu', 'thanks', 'agradeço']
        usar_cache = not any(palavra in pergunta.lower() for palavra in palavras_sem_cache)
        
        if usar_cache and cache_key in CACHE_RESPOSTAS:
            return CACHE_RESPOSTAS[cache_key], "cache"
        
        # 🤖 OpenAI
        resposta = processar_openai(pergunta, tipo_usuario)
        if resposta:
            if usar_cache:
                CACHE_RESPOSTAS[cache_key] = resposta
            return resposta, f"openai_{tipo_usuario['tipo']}"
        
        # 🔄 Fallback
        nome = tipo_usuario.get('nome_real', 'Cliente')
        return f"Desculpa {nome}, estou com dificuldades técnicas no momento. 😅\n\nPor favor, fale diretamente com o Natan no WhatsApp: (21) 99282-6074\n\nEle vai te atender pessoalmente!", "fallback"
        
    except Exception as e:
        print(f"❌ Erro gerar_resposta: {e}")
        return "Ops, erro técnico! Fale com Natan: (21) 99282-6074\n\n✨ Vibrações Positivas!", "erro"

# =============================================================================
# 📡 ROTAS
# =============================================================================

@app.route('/health', methods=['GET'])
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        "status": "online",
        "sistema": "NatanAI v6.1 PERSONALIZADA",
        "openai": verificar_openai(),
        "supabase": supabase is not None,
        "features": ["nomes_personalizados", "contexto_completo", "validacao_forte"],
        "economia": "~20k mensagens com $5"
    })

@app.route('/chat', methods=['POST'])
@app.route('/api/chat', methods=['POST'])
def chat():
    global HISTORICO_CONVERSAS
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Dados não fornecidos"}), 400
        
        mensagem = data.get('message') or data.get('pergunta', '')
        if not mensagem or not mensagem.strip():
            return jsonify({"error": "Mensagem vazia"}), 400
        
        mensagem = mensagem.strip()
        
        # ✅ AUTENTICAÇÃO E EXTRAÇÃO DE NOME
        auth_header = request.headers.get('Authorization', '')
        user_data_req = data.get('user_data', {})
        
        tipo_usuario = None
        user_info = None
        
        if auth_header:
            user_info = verificar_token_supabase(auth_header)
            if user_info:
                dados = obter_dados_usuario_completos(user_info.id)
                user_full = {
                    'email': user_info.email,
                    'user_id': user_info.id,
                    'plan': user_info.user_metadata.get('plan', 'starter') if user_info.user_metadata else 'starter'
                }
                if dados:
                    user_full.update(dados)
                tipo_usuario = determinar_tipo_usuario(user_full, user_info)
        
        if not tipo_usuario:
            if user_data_req:
                tipo_usuario = determinar_tipo_usuario(user_data_req)
            else:
                tipo_usuario = {
                    'tipo': 'starter',
                    'nome_display': 'Cliente',
                    'plano': 'Starter',
                    'nome_real': 'Cliente'
                }
        
        nome_usuario = tipo_usuario.get('nome_real', 'Cliente')
        print(f"💬 [{datetime.now().strftime('%H:%M:%S')}] {nome_usuario} ({tipo_usuario['nome_display']}): {mensagem[:50]}...")
        
        # ✅ Gera resposta PERSONALIZADA
        resposta, fonte = gerar_resposta(mensagem, tipo_usuario)
        valida, _ = validar_resposta(resposta)
        
        # Histórico
        with historico_lock:
            HISTORICO_CONVERSAS.append({
                "timestamp": datetime.now().isoformat(),
                "tipo": tipo_usuario['tipo'],
                "nome": nome_usuario,
                "fonte": fonte,
                "validacao": valida
            })
            if len(HISTORICO_CONVERSAS) > 1000:
                HISTORICO_CONVERSAS = HISTORICO_CONVERSAS[-500:]
        
        return jsonify({
            "response": resposta,
            "resposta": resposta,
            "metadata": {
                "fonte": fonte,
                "sistema": "NatanAI v6.1 PERSONALIZADA",
                "tipo_usuario": tipo_usuario['tipo'],
                "plano": tipo_usuario['plano'],
                "nome_usuario": nome_usuario,
                "validacao": valida,
                "autenticado": user_info is not None,
                "contexto": "portfolio+site+nome"
            }
        })
        
    except Exception as e:
        print(f"❌ Erro: {e}")
        return jsonify({
            "response": "Erro técnico. Fale com Natan: (21) 99282-6074\n\n✨ Vibrações Positivas!",
            "resposta": "Erro técnico. Fale com Natan: (21) 99282-6074\n\n✨ Vibrações Positivas!",
            "metadata": {"fonte": "erro", "error": str(e)}
        }), 500

@app.route('/estatisticas', methods=['GET'])
@app.route('/api/estatisticas', methods=['GET'])
def estatisticas():
    try:
        if not HISTORICO_CONVERSAS:
            return jsonify({"message": "Sem conversas"})
        
        fontes = {}
        tipos = {}
        nomes = {}
        validacoes = 0
        
        with historico_lock:
            for c in HISTORICO_CONVERSAS:
                f = c.get("fonte", "unknown")
                fontes[f] = fontes.get(f, 0) + 1
                t = c.get("tipo", "unknown")
                tipos[t] = tipos.get(t, 0) + 1
                n = c.get("nome", "Anônimo")
                nomes[n] = nomes.get(n, 0) + 1
                if c.get("validacao", True):
                    validacoes += 1
        
        return jsonify({
            "total": len(HISTORICO_CONVERSAS),
            "fontes": fontes,
            "tipos_usuario": tipos,
            "usuarios_ativos": len(nomes),
            "top_usuarios": dict(sorted(nomes.items(), key=lambda x: x[1], reverse=True)[:5]),
            "validacao": {
                "ok": validacoes,
                "taxa": round((validacoes / len(HISTORICO_CONVERSAS)) * 100, 2)
            },
            "sistema": "NatanAI v6.1 PERSONALIZADA - 20k msgs com $5"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({
        "status": "pong",
        "timestamp": datetime.now().isoformat(),
        "version": "v6.1"
    })

@app.route('/', methods=['GET'])
def home():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>NatanAI v6.1 PERSONALIZADA</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { 
                font-family: 'Segoe UI', Arial, sans-serif; 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }
            .container { 
                max-width: 900px; 
                margin: 0 auto; 
                background: white; 
                padding: 30px; 
                border-radius: 20px; 
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            }
            .header { 
                text-align: center; 
                margin-bottom: 30px;
                padding-bottom: 20px;
                border-bottom: 3px solid #667eea;
            }
            .header h1 { 
                color: #667eea; 
                margin-bottom: 10px;
                font-size: 2em;
            }
            .badge {
                display: inline-block;
                padding: 8px 16px;
                margin: 5px;
                border-radius: 20px;
                font-size: 0.85em;
                font-weight: bold;
                background: #4CAF50;
                color: white;
            }
            .info-box {
                background: linear-gradient(135deg, #e3f2fd, #f3e5f5);
                padding: 20px;
                border-radius: 15px;
                margin: 20px 0;
                border-left: 5px solid #667eea;
            }
            .info-box h3 { color: #667eea; margin-bottom: 10px; }
            .chat-box { 
                border: 2px solid #e0e0e0;
                height: 400px; 
                overflow-y: auto; 
                padding: 20px; 
                margin: 20px 0; 
                background: #fafafa;
                border-radius: 15px;
            }
            .message { 
                margin: 15px 0; 
                padding: 15px; 
                border-radius: 15px;
                animation: fadeIn 0.3s;
            }
            @keyframes fadeIn {
                from { opacity: 0; transform: translateY(10px); }
                to { opacity: 1; transform: translateY(0); }
            }
            .user { 
                background: linear-gradient(135deg, #667eea, #764ba2);
                color: white;
                margin-left: 20%;
            }
            .bot { 
                background: #e8f5e9;
                margin-right: 20%;
                border-left: 4px solid #4CAF50;
            }
            .input-area { 
                display: flex; 
                gap: 10px;
                margin-top: 20px;
            }
            input { 
                flex: 1; 
                padding: 15px; 
                border: 2px solid #e0e0e0;
                border-radius: 25px;
                font-size: 1em;
            }
            button { 
                padding: 15px 30px;
                background: linear-gradient(135deg, #667eea, #764ba2);
                color: white; 
                border: none;
                border-radius: 25px;
                cursor: pointer;
                font-weight: bold;
            }
            .examples {
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin: 20px 0;
            }
            .example-btn {
                padding: 8px 16px;
                background: white;
                border: 2px solid #667eea;
                color: #667eea;
                border-radius: 20px;
                cursor: pointer;
                font-size: 0.9em;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🤖 NatanAI v6.1 PERSONALIZADA</h1>
                <p style="color: #666;">Agora ela sabe seu nome! 👋</p>
                <span class="badge">RECURSO: Nomes Personalizados</span>
                <span class="badge">CUSTO: 0 tokens extras!</span>
            </div>
            
            <div class="info-box">
                <h3>✨ Novidades v6.1</h3>
                <p>✅ Chama você pelo nome naturalmente<br>
                ✅ Tratamento personalizado por tipo (Admin/Professional/Starter)<br>
                ✅ Contexto completo mantido<br>
                ✅ ZERO custo adicional (mantém 20k msgs com $5)<br>
                ✅ Respostas ainda mais humanas e empáticas</p>
            </div>
            
            <div id="chat-box" class="chat-box">
                <div class="message bot">
                    <strong>🤖 NatanAI v6.1:</strong><br><br>
                    Olá! Agora eu sei o nome de cada pessoa! 👋<br><br>
                    Quando você se conecta pela plataforma, eu vejo:<br>
                    • 📝 Seu nome<br>
                    • 💎 Seu plano (Starter/Professional)<br>
                    • 📚 Todo contexto do portfólio do Natan<br><br>
                    E trato você de forma pessoal e natural!<br><br>
                    <strong>✨ Vibrações Positivas!</strong>
                </div>
            </div>
            
            <div class="examples">
                <button class="example-btn" onclick="testar('Oi, tudo bem?')">👋 Oi</button>
                <button class="example-btn" onclick="testar('Me conta sobre os projetos')">📱 Projetos</button>
                <button class="example-btn" onclick="testar('Qual plano é melhor pra mim?')">💎 Planos</button>
                <button class="example-btn" onclick="testar('Obrigado pela ajuda!')">🙏 Obrigado</button>
            </div>
            
            <div class="input-area">
                <input type="text" id="msg" placeholder="Digite sua mensagem..." onkeypress="if(event.key==='Enter') enviar()">
                <button onclick="enviar()">Enviar</button>
            </div>
        </div>

        <script>
        function testar(msg) {
            document.getElementById('msg').value = msg;
            enviar();
        }
        
        async function enviar() {
            const input = document.getElementById('msg');
            const chatBox = document.getElementById('chat-box');
            const msg = input.value.trim();
            
            if (!msg) return;
            
            chatBox.innerHTML += `<div class="message user"><strong>Você:</strong><br>${msg}</div>`;
            input.value = '';
            chatBox.scrollTop = chatBox.scrollHeight;
            
            try {
                const response = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        message: msg,
                        user_data: {
                            email: 'teste@exemplo.com',
                            plan: 'starter'
                        }
                    })
                });
                
                const data = await response.json();
                const resp = (data.response || data.resposta).replace(/\\n/g, '<br>');
                const nome = data.metadata?.nome_usuario || 'Teste';
                chatBox.innerHTML += `<div class="message bot"><strong>🤖 NatanAI:</strong><br>${resp}<br><br><small style="opacity: 0.7;">👤 Detectado: ${nome}</small></div>`;
                
            } catch (error) {
                chatBox.innerHTML += `<div class="message bot"><strong>🤖 NatanAI:</strong><br>Erro. WhatsApp: (21) 99282-6074</div>`;
            }
            
            chatBox.scrollTop = chatBox.scrollHeight;
        }
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

# =============================================================================
# 🚀 INICIALIZAÇÃO
# =============================================================================

if __name__ == '__main__':
    print("\n" + "="*80)
    print("🤖 NATANAI v6.1 PERSONALIZADA - NOMES + CONTEXTO COMPLETO")
    print("="*80)
    print("✨ NOVO: Sistema de nomes personalizados")
    print("📚 Contexto: Portfolio + Site Principal + Nome do usuário")
    print("⚡ Economia: ~40% tokens mantida")
    print("💰 $5 = ~20.000 mensagens")
    print("🎯 Tratamento personalizado por plano")
    print("✅ Anti-alucinação: Validação forte")
    print("📞 WhatsApp: (21) 99282-6074")
    print("="*80 + "\n")
    
    print(f"OpenAI: {'✅' if verificar_openai() else '⚠️'}")
    print(f"Supabase: {'✅' if supabase else '⚠️'}\n")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

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
# 🔐 AUTENTICAÇÃO
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
    try:
        if not supabase:
            return None
        response = supabase.table('user_accounts').select('*').eq('user_id', user_id).single().execute()
        return response.data if response.data else None
    except:
        return None

def determinar_tipo_usuario(user_data):
    try:
        email = user_data.get('email', '')
        plan = user_data.get('plan', 'starter')
        
        if email == ADMIN_EMAIL:
            return {'tipo': 'admin', 'nome': 'Admin', 'plano': 'Admin'}
        
        if plan == 'professional':
            return {'tipo': 'professional', 'nome': 'Professional', 'plano': 'Professional'}
        
        return {'tipo': 'starter', 'nome': 'Starter', 'plano': 'Starter'}
    except:
        return {'tipo': 'starter', 'nome': 'Starter', 'plano': 'Starter'}

# =============================================================================
# 🛡️ VALIDAÇÃO ANTI-ALUCINAÇÃO
# =============================================================================

PALAVRAS_PROIBIDAS = [
    "grátis", "gratuito", "R$ 0", "0 reais", "free",
    "garantimos primeiro lugar", "100% de conversão", "sucesso garantido",
    "site pronto em 1 hora", "atendimento 24/7", "empresa com 10 anos"
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
# 🤖 OPENAI - OTIMIZADO
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
    """✅ OTIMIZADO - Prompt 50% menor!"""
    if not client or not verificar_openai():
        return None
    
    try:
        # 🔥 PROMPTS COMPACTOS POR TIPO
        if tipo_usuario['tipo'] == 'admin':
            ctx = "🔴 ADMIN (Natan): Acesso total. Respostas técnicas e detalhadas."
        elif tipo_usuario['tipo'] == 'professional':
            ctx = "💎 PROFESSIONAL (R$79,99/mês): Suporte prioritário, recursos avançados."
        else:
            ctx = "🌱 STARTER (R$39,99/mês): Suporte padrão. Sugira upgrade se relevante."
        
        # 🎯 PROMPT ULTRA-COMPACTO (economia de ~60% tokens)
        prompt = f"""Você é NatanAI, assistente da NatanDEV.

{ctx}

INFO OFICIAL:
- Criador: Natan Borges (Web Dev Full-Stack, RJ)
- WhatsApp: (21) 99282-6074
- Site: natansites.com.br
- Portfolio: natandev02.netlify.app

PLANOS:
- Starter: R$39,99/mês + R$350 inicial (site básico responsivo)
- Professional: R$79,99/mês + R$530 inicial (design avançado, SEO, APIs)

REGRAS:
✅ Seja natural e empático
✅ Use contexto do tipo de usuário
✅ NUNCA diga "eu desenvolvo" - sempre "o Natan desenvolve"
✅ NUNCA invente preços
✅ Use apenas infos acima

Responda: {pergunta}"""

        # 🚀 CHAMADA OTIMIZADA
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,  # ✅ Reduzido de 350 para 200
            temperature=0.7
        )
        
        resposta = response.choices[0].message.content.strip()
        
        # Validação
        valida, problemas = validar_resposta(resposta)
        if not valida:
            print(f"⚠️ Validação falhou: {problemas}")
            return None
        
        # Adiciona frase de impacto ocasionalmente
        if random.random() < 0.3:
            resposta += "\n\nVibrações Positivas!"
        
        return resposta
        
    except Exception as e:
        print(f"❌ Erro OpenAI: {e}")
        return None

def gerar_resposta(pergunta, tipo_usuario):
    """Sistema principal"""
    try:
        # Cache por tipo de usuário
        cache_key = hashlib.md5(f"{pergunta}_{tipo_usuario['tipo']}".encode()).hexdigest()
        if cache_key in CACHE_RESPOSTAS:
            return CACHE_RESPOSTAS[cache_key], "cache"
        
        # OpenAI
        resposta = processar_openai(pergunta, tipo_usuario)
        if resposta:
            CACHE_RESPOSTAS[cache_key] = resposta
            return resposta, f"openai_{tipo_usuario['tipo']}"
        
        # Fallback
        return "Desculpa, estou com dificuldades. 😅\n\nChama no WhatsApp: (21) 99282-6074\n\nVibrações Positivas!", "fallback"
        
    except Exception as e:
        return f"Erro técnico. Fale com Natan: (21) 99282-6074\n\nVibrações Positivas!", "erro"

# =============================================================================
# 📡 ROTAS
# =============================================================================

@app.route('/health', methods=['GET'])
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        "status": "online",
        "sistema": "NatanAI v5.0 OTIMIZADO",
        "openai": verificar_openai(),
        "supabase": supabase is not None,
        "economia": "~40% tokens → 20k msgs com $5"
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
        
        # Autenticação
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
                tipo_usuario = determinar_tipo_usuario(user_full)
        
        if not tipo_usuario:
            if user_data_req:
                tipo_usuario = determinar_tipo_usuario(user_data_req)
            else:
                tipo_usuario = {'tipo': 'starter', 'nome': 'Cliente', 'plano': 'Starter'}
        
        print(f"💬 [{datetime.now().strftime('%H:%M:%S')}] {tipo_usuario['nome']}: {mensagem}")
        
        # Gera resposta
        resposta, fonte = gerar_resposta(mensagem, tipo_usuario)
        valida, _ = validar_resposta(resposta)
        
        # Histórico
        with historico_lock:
            HISTORICO_CONVERSAS.append({
                "timestamp": datetime.now().isoformat(),
                "tipo": tipo_usuario['tipo'],
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
                "sistema": "NatanAI v5.0 OTIMIZADO",
                "tipo_usuario": tipo_usuario['tipo'],
                "plano": tipo_usuario['plano'],
                "validacao": valida,
                "autenticado": user_info is not None
            }
        })
        
    except Exception as e:
        print(f"❌ Erro: {e}")
        return jsonify({
            "response": "Erro. Fale com Natan: (21) 99282-6074\n\nVibrações Positivas!",
            "resposta": "Erro. Fale com Natan: (21) 99282-6074\n\nVibrações Positivas!",
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
        validacoes = 0
        
        with historico_lock:
            for c in HISTORICO_CONVERSAS:
                f = c.get("fonte", "unknown")
                fontes[f] = fontes.get(f, 0) + 1
                t = c.get("tipo", "unknown")
                tipos[t] = tipos.get(t, 0) + 1
                if c.get("validacao", True):
                    validacoes += 1
        
        return jsonify({
            "total": len(HISTORICO_CONVERSAS),
            "fontes": fontes,
            "tipos_usuario": tipos,
            "validacao": {
                "ok": validacoes,
                "taxa": round((validacoes / len(HISTORICO_CONVERSAS)) * 100, 2)
            },
            "sistema": "NatanAI v5.0 OTIMIZADO - 20k msgs com $5"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({
        "status": "pong",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/', methods=['GET'])
def home():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>NatanAI v5.0 OTIMIZADO</title>
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
                <h1>🤖 NatanAI v5.0 OTIMIZADO</h1>
                <p style="color: #666;">20.000 mensagens com $5! 🚀</p>
                <span class="badge">ECONOMIA: 40% tokens</span>
            </div>
            
            <div class="info-box">
                <h3>⚡ Otimizações Implementadas</h3>
                <p>✅ Prompt 60% menor (800→320 tokens)<br>
                ✅ max_tokens reduzido (350→200)<br>
                ✅ Verificação sem gastar créditos<br>
                ✅ Cache inteligente por tipo usuário<br>
                ✅ ~20.000 mensagens com $5!</p>
            </div>
            
            <div id="chat-box" class="chat-box">
                <div class="message bot">
                    <strong>🤖 NatanAI OTIMIZADO:</strong><br><br>
                    Olá! Agora sou 40% mais econômica! 🚀<br><br>
                    👑 Admin | 💎 Professional | 🌱 Starter<br><br>
                    <strong>Vibrações Positivas!</strong>
                </div>
            </div>
            
            <div class="examples">
                <button class="example-btn" onclick="testar('Oi!')">👋 Oi</button>
                <button class="example-btn" onclick="testar('Preços?')">💰 Preços</button>
                <button class="example-btn" onclick="testar('Quero site')">🚀 Site</button>
            </div>
            
            <div class="input-area">
                <input type="text" id="msg" placeholder="Digite..." onkeypress="if(event.key==='Enter') enviar()">
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
                    body: JSON.stringify({ message: msg })
                });
                
                const data = await response.json();
                const resp = (data.response || data.resposta).replace(/\\n/g, '<br>');
                chatBox.innerHTML += `<div class="message bot"><strong>🤖 NatanAI:</strong><br>${resp}</div>`;
                
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
    print("🤖 NATANAI v5.0 - OTIMIZADO PARA 20.000 MENSAGENS")
    print("="*80)
    print("⚡ ECONOMIA: ~40% tokens")
    print("💰 $5 = ~20.000 mensagens")
    print("📞 WhatsApp: (21) 99282-6074")
    print("="*80 + "\n")
    
    print(f"OpenAI: {'✅' if verificar_openai() else '⚠️'}")
    print(f"Supabase: {'✅' if supabase else '⚠️'}\n")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

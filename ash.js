const solanaWeb3 = require('@solana/web3.js');
const fs = require('fs');
const TelegramBot = require('node-telegram-bot-api');
require('dotenv').config();

// ==========================================
// ⚙️ CONFIGURATION (via .env)
// ==========================================
const HELIUS_API_KEY = process.env.HELIUS_API_KEY;
const TELEGRAM_TOKEN = process.env.TELEGRAM_TOKEN;
const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID;

if (!HELIUS_API_KEY || !TELEGRAM_TOKEN || !TELEGRAM_CHAT_ID) {
    console.error('[X] Variables manquantes dans .env : HELIUS_API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID');
    process.exit(1);
}

const RAYDIUM_PROGRAM_ID = '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8';
const PUMPFUN_PROGRAM_ID = '6EF8rrecthR5Dkzon8Nwu78hRvfX4lfs7SihUvX4u8D';
const WSOL_ADDRESS = 'So11111111111111111111111111111111111111112';

const HELIUS_RPC_URL = `https://mainnet.helius-rpc.com/?api-key=${HELIUS_API_KEY}`;
const HELIUS_WSS_URL = `wss://mainnet.helius-rpc.com/?api-key=${HELIUS_API_KEY}`;

// ==========================================
// 🔧 PARAMÈTRES AJUSTABLES
// ==========================================
const SIGNATURE_CACHE_MAX = 5000;
const SIGNATURE_CACHE_PURGE_INTERVAL = 30;
const DYNAMIC_WALLET_TTL = 4 * 60 * 60 * 1000;
const RPC_DELAY_MS = 500;
const HEARTBEAT_INTERVAL = 60 * 1000;
const TX_FETCH_DELAY = 2000;

// ==========================================
// 🔌 CONNEXION SOLANA
// ==========================================
let solanaConnection = createConnection();

function createConnection() {
    return new solanaWeb3.Connection(HELIUS_RPC_URL, {
        wsEndpoint: HELIUS_WSS_URL,
        commitment: 'confirmed',
    });
}

const bot = new TelegramBot(TELEGRAM_TOKEN, {
    polling: {
        params: {
            allowed_updates: ['message', 'callback_query']
        }
    }
});

const processedSignatures = new Set();
const walletsUnderSurveillance = new Map();
let activeSubscriptions = new Map();
let targetWallets = new Map();
let blacklist = new Set();
let isPaused = false;

// État pour les interactions en attente (ajout, blacklist, rename, remove)
const pendingActions = new Map();

// ==========================================
// 🔒 SÉCURITÉ TELEGRAM
// ==========================================
function isAuthorized(msg) {
    return msg.chat.id.toString() === TELEGRAM_CHAT_ID;
}

function isAuthorizedCallback(query) {
    return query.message.chat.id.toString() === TELEGRAM_CHAT_ID;
}

// ==========================================
// 📊 RATE LIMITER
// ==========================================
class RateLimiter {
    constructor(minDelay) {
        this.minDelay = minDelay;
        this.lastCall = 0;
    }

    async wait() {
        const now = Date.now();
        const elapsed = now - this.lastCall;
        if (elapsed < this.minDelay) {
            await new Promise(r => setTimeout(r, this.minDelay - elapsed));
        }
        this.lastCall = Date.now();
    }
}

const rpcLimiter = new RateLimiter(RPC_DELAY_MS);

// ==========================================
// 📬 TELEGRAM MESSAGE QUEUE (anti-429)
// ==========================================
const TG_MIN_DELAY_MS = 350;        // Délai minimum entre chaque message (Telegram limite ~30 msg/sec en groupe)
const TG_MAX_RETRIES = 5;           // Nombre max de retries sur 429
const TG_BASE_BACKOFF_MS = 1000;    // Backoff de base (doublé à chaque retry)

class TelegramQueue {
    constructor() {
        this.queue = [];
        this.processing = false;
        this.lastSendTime = 0;
    }

    enqueue(chatId, text, options) {
        return new Promise((resolve, reject) => {
            this.queue.push({ chatId, text, options, resolve, reject });
            if (!this.processing) {
                this.processQueue();
            }
        });
    }

    async processQueue() {
        if (this.processing) return;
        this.processing = true;

        while (this.queue.length > 0) {
            const item = this.queue.shift();

            // Respecter le délai minimum entre les envois
            const now = Date.now();
            const elapsed = now - this.lastSendTime;
            if (elapsed < TG_MIN_DELAY_MS) {
                await new Promise(r => setTimeout(r, TG_MIN_DELAY_MS - elapsed));
            }

            try {
                const result = await this.sendWithRetry(item.chatId, item.text, item.options);
                this.lastSendTime = Date.now();
                item.resolve(result);
            } catch (e) {
                console.error('[X] Telegram queue - échec définitif :', e.message);
                item.reject(e);
            }
        }

        this.processing = false;
    }

    async sendWithRetry(chatId, text, options, attempt = 0) {
        try {
            return await bot.sendMessage(chatId, text, options);
        } catch (e) {
            const is429 = e.response && e.response.statusCode === 429;
            if (is429 && attempt < TG_MAX_RETRIES) {
                // Extraire le Retry-After du header ou du body
                let retryAfter = TG_BASE_BACKOFF_MS * Math.pow(2, attempt);
                if (e.response && e.response.body && e.response.body.parameters && e.response.body.parameters.retry_after) {
                    retryAfter = e.response.body.parameters.retry_after * 1000;
                }
                console.log('[⏳] Telegram 429 — retry ' + (attempt + 1) + '/' + TG_MAX_RETRIES + ' dans ' + retryAfter + 'ms');
                await new Promise(r => setTimeout(r, retryAfter));
                return this.sendWithRetry(chatId, text, options, attempt + 1);
            }
            throw e;
        }
    }
}

const tgQueue = new TelegramQueue();

// Fonction unique pour envoyer un message Telegram via la queue
function safeSend(chatId, text, options) {
    return tgQueue.enqueue(chatId, text, options || {});
}

// ==========================================
// 💾 GESTION DE LA BASE DE DONNÉES
// ==========================================
const DB_FILE = 'database.json';
const BLACKLIST_FILE = 'blacklist.json';

function loadDatabase() {
    if (fs.existsSync(DB_FILE)) {
        try {
            const data = JSON.parse(fs.readFileSync(DB_FILE, 'utf8'));
            for (const [wallet, config] of Object.entries(data)) {
                if (!config.label) config.label = null;
                targetWallets.set(wallet, config);
            }
            console.log(`[💾] Base de données chargée : ${targetWallets.size} cibles.`);
        } catch (e) {
            console.error('[X] Erreur de lecture de la base de données :', e.message);
        }
    }
}

function saveDatabase() {
    const dataObj = {};
    for (const [wallet, config] of targetWallets.entries()) {
        dataObj[wallet] = config;
    }
    fs.writeFileSync(DB_FILE, JSON.stringify(dataObj, null, 2));
}

function loadBlacklist() {
    if (fs.existsSync(BLACKLIST_FILE)) {
        try {
            const data = JSON.parse(fs.readFileSync(BLACKLIST_FILE, 'utf8'));
            for (const wallet of data) {
                blacklist.add(wallet);
            }
            console.log(`[🚫] Blacklist chargée : ${blacklist.size} wallet(s).`);
        } catch (e) {
            console.error('[X] Erreur de lecture de la blacklist :', e.message);
        }
    }
}

function saveBlacklist() {
    fs.writeFileSync(BLACKLIST_FILE, JSON.stringify([...blacklist], null, 2));
}

loadDatabase();
loadBlacklist();

// ==========================================
// 🏷️ UTILITAIRES
// ==========================================
function shortAddr(wallet) {
    return wallet.slice(0, 6) + '...' + wallet.slice(-4);
}

function getWalletDisplay(wallet) {
    const config = targetWallets.get(wallet);
    if (config && config.label) {
        return '<b>' + config.label + '</b> (<code>' + wallet + '</code>)';
    }
    return '<code>' + wallet + '</code>';
}

// ==========================================
// 🪙 INFOS TOKEN ENRICHIES (Helius DAS API)
// ==========================================
async function fetchTokenMetadata(mintAddress) {
    try {
        await rpcLimiter.wait();
        const response = await fetch(HELIUS_RPC_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                jsonrpc: '2.0',
                id: 'token-metadata',
                method: 'getAsset',
                params: { id: mintAddress },
            }),
        });
        const data = await response.json();
        if (data && data.result) {
            const asset = data.result;
            const name = (asset.content && asset.content.metadata && asset.content.metadata.name) || null;
            const symbol = (asset.content && asset.content.metadata && asset.content.metadata.symbol) || null;
            const supply = (asset.token_info && asset.token_info.supply) || null;
            const decimals = (asset.token_info && asset.token_info.decimals) || 0;

            let formattedSupply = null;
            if (supply !== null) {
                const realSupply = supply / Math.pow(10, decimals);
                formattedSupply = formatNumber(realSupply);
            }
            return { name: name, symbol: symbol, supply: formattedSupply };
        }
    } catch (e) {
        console.error('[X] Erreur fetchTokenMetadata :', e.message);
    }
    return { name: null, symbol: null, supply: null };
}

function formatNumber(num) {
    if (num >= 1000000000) return (num / 1000000000).toFixed(2) + 'B';
    if (num >= 1000000) return (num / 1000000).toFixed(2) + 'M';
    if (num >= 1000) return (num / 1000).toFixed(2) + 'K';
    return num.toFixed(2);
}

// ==========================================
// 🧹 NETTOYAGE AUTOMATIQUE
// ==========================================
setInterval(function() {
    if (processedSignatures.size > SIGNATURE_CACHE_MAX) {
        processedSignatures.clear();
        console.log('[🧹] Cache des signatures purgé.');
    }
}, SIGNATURE_CACHE_PURGE_INTERVAL * 60 * 1000);

setInterval(function() {
    const now = Date.now();
    let cleaned = 0;
    for (const [wallet, info] of walletsUnderSurveillance.entries()) {
        if (now - info.addedAt > DYNAMIC_WALLET_TTL) {
            try {
                solanaConnection.removeOnLogsListener(info.subId);
            } catch (e) {
                console.error('[X] Erreur suppression sub ' + wallet + ' :', e.message);
            }
            walletsUnderSurveillance.delete(wallet);
            cleaned++;
        }
    }
    if (cleaned > 0) {
        console.log('[🧹] ' + cleaned + ' wallet(s) dynamique(s) expirés nettoyés.');
    }
}, 10 * 60 * 1000);

// ==========================================
// 💓 HEARTBEAT & RECONNEXION WEBSOCKET
// ==========================================
let lastLogReceived = Date.now();

setInterval(async function() {
    const silenceDuration = Date.now() - lastLogReceived;
    if (silenceDuration > 5 * 60 * 1000 && targetWallets.size > 0) {
        console.warn('[⚠️] Aucun log reçu depuis 5 min. Reconnexion...');
        await reconnectAll();
    }
}, HEARTBEAT_INTERVAL);

async function reconnectAll() {
    try {
        for (const [wallet, subId] of activeSubscriptions.entries()) {
            try { solanaConnection.removeOnLogsListener(subId); } catch (e) {}
        }
        activeSubscriptions.clear();

        for (const [wallet, info] of walletsUnderSurveillance.entries()) {
            try { solanaConnection.removeOnLogsListener(info.subId); } catch (e) {}
        }
        walletsUnderSurveillance.clear();

        solanaConnection = createConnection();
        targetWallets.forEach(function(config, wallet) { startTracking(wallet); });

        lastLogReceived = Date.now();
        console.log('[✅] Reconnexion réussie.');
        await sendTG('🔄 <b>Reconnexion WebSocket effectuée.</b> Tous les wallets réabonnés.');
    } catch (e) {
        console.error('[X] Échec de reconnexion :', e.message);
    }
}

// ==========================================
// 🎛️ INTERFACE TELEGRAM — MENUS & BOUTONS
// ==========================================

function sendMainMenu(chatId) {
    safeSend(chatId, '🎛️ <b>RADAR V12 — MENU PRINCIPAL</b>\n\nChoisissez une action :', {
        parse_mode: 'HTML',
        reply_markup: {
            inline_keyboard: [
                [
                    { text: '➕ Ajouter cible', callback_data: 'action_add' },
                    { text: '🗑️ Supprimer cible', callback_data: 'action_remove' }
                ],
                [
                    { text: '🏷️ Renommer', callback_data: 'action_rename' },
                    { text: '📊 Statut', callback_data: 'action_status' }
                ],
                [
                    { text: '🚫 Blacklister', callback_data: 'action_blacklist' },
                    { text: '✅ Whitelister', callback_data: 'action_whitelist' }
                ],
                [
                    { text: '📋 Voir blacklist', callback_data: 'action_blacklisted' },
                    { text: '📈 Stats', callback_data: 'action_stats' }
                ],
                [
                    { text: isPaused ? '▶️ Reprendre' : '⏸️ Pause', callback_data: isPaused ? 'action_resume' : 'action_pause' },
                    { text: '🧹 Vider cache', callback_data: 'action_clear' }
                ],
                [
                    { text: '📖 Aide', callback_data: 'action_help' }
                ]
            ]
        }
    });
}

// --- /menu ou /start → Menu principal ---
bot.onText(/\/(menu|start)/, function(msg) {
    if (!isAuthorized(msg)) return;
    sendMainMenu(TELEGRAM_CHAT_ID);
});

// --- /help (texte) ---
bot.onText(/\/help$/, function(msg) {
    if (!isAuthorized(msg)) return;
    sendHelpMessage();
});

function sendHelpMessage() {
    var helpText = '📖 <b>COMMANDES DU RADAR V12</b>\n\n' +
        '<b>📡 Surveillance :</b>\n' +
        '/add <code>&lt;wallet&gt; &lt;min&gt; &lt;max&gt; [nom]</code>\n' +
        '/remove <code>&lt;wallet&gt;</code>\n' +
        '/rename <code>&lt;wallet&gt; &lt;nom&gt;</code>\n' +
        '/status\n\n' +
        '<b>🚫 Blacklist :</b>\n' +
        '/blacklist <code>&lt;wallet&gt;</code>\n' +
        '/whitelist <code>&lt;wallet&gt;</code>\n' +
        '/blacklisted\n\n' +
        '<b>⚙️ Contrôle :</b>\n' +
        '/stats /pause /resume /clear\n' +
        '/menu → Menu avec boutons\n' +
        '/help → Cette aide';
    safeSend(TELEGRAM_CHAT_ID, helpText, { parse_mode: 'HTML' });
}

// ==========================================
// 🔘 GESTION DES CALLBACKS (boutons)
// ==========================================

bot.on('callback_query', async function(query) {
    console.log('[🔘] Callback reçu :', query.data);

    if (!isAuthorizedCallback(query)) {
        console.log('[🔘] Non autorisé, chat id:', query.message.chat.id);
        return;
    }

    const chatId = query.message.chat.id;
    const data = query.data;
    if (!data) return;

    // Accusé de réception du bouton (avec retry sur 429)
    try {
        await bot.answerCallbackQuery(query.id);
    } catch (e) {
        if (e.response && e.response.statusCode === 429) {
            const retryMs = (e.response.body && e.response.body.parameters && e.response.body.parameters.retry_after)
                ? e.response.body.parameters.retry_after * 1000 : 1000;
            await new Promise(r => setTimeout(r, retryMs));
            try { await bot.answerCallbackQuery(query.id); } catch (e2) {}
        }
    }

    // --- Actions immédiates ---
    if (data === 'action_status') {
        sendStatusMessage();
        return;
    }

    if (data === 'action_stats') {
        sendStatsMessage();
        return;
    }

    if (data === 'action_help') {
        sendHelpMessage();
        return;
    }

    if (data === 'action_blacklisted') {
        sendBlacklistMessage();
        return;
    }

    if (data === 'action_pause') {
        isPaused = true;
        safeSend(chatId, '⏸️ <b>Radar en pause.</b>\nUtilisez /menu pour reprendre.', { parse_mode: 'HTML' });
        return;
    }

    if (data === 'action_resume') {
        isPaused = false;
        safeSend(chatId, '▶️ <b>Radar repris !</b> Surveillance active.', { parse_mode: 'HTML' });
        return;
    }

    if (data === 'action_clear') {
        var count = processedSignatures.size;
        processedSignatures.clear();
        safeSend(chatId, '🧹 <b>Cache vidé :</b> ' + count + ' signatures supprimées.', { parse_mode: 'HTML' });
        return;
    }

    // --- Actions avec saisie ---
    if (data === 'action_add') {
        pendingActions.set(chatId.toString(), { type: 'add', step: 'wallet' });
        safeSend(chatId, '➕ <b>AJOUTER UNE CIBLE</b>\n\nEnvoyez l\'adresse du wallet à surveiller :', {
            parse_mode: 'HTML',
            reply_markup: { inline_keyboard: [[{ text: '❌ Annuler', callback_data: 'action_cancel' }]] }
        });
        return;
    }

    if (data === 'action_remove') {
        if (targetWallets.size === 0) {
            safeSend(chatId, '⚠️ Aucune cible à supprimer.');
            return;
        }
        // Afficher les cibles comme boutons
        var removeButtons = [];
        for (const [w, config] of targetWallets.entries()) {
            var btnLabel = config.label ? config.label + ' (' + shortAddr(w) + ')' : shortAddr(w);
            removeButtons.push([{ text: '🗑️ ' + btnLabel, callback_data: 'rm_' + w }]);
        }
        removeButtons.push([{ text: '❌ Annuler', callback_data: 'action_cancel' }]);
        safeSend(chatId, '🗑️ <b>SUPPRIMER UNE CIBLE</b>\n\nChoisissez le wallet à supprimer :', {
            parse_mode: 'HTML',
            reply_markup: { inline_keyboard: removeButtons }
        });
        return;
    }

    if (data === 'action_rename') {
        if (targetWallets.size === 0) {
            safeSend(chatId, '⚠️ Aucune cible à renommer.');
            return;
        }
        var renameButtons = [];
        for (const [w, config] of targetWallets.entries()) {
            var btnLabel2 = config.label ? config.label + ' (' + shortAddr(w) + ')' : shortAddr(w);
            renameButtons.push([{ text: '🏷️ ' + btnLabel2, callback_data: 'ren_' + w }]);
        }
        renameButtons.push([{ text: '❌ Annuler', callback_data: 'action_cancel' }]);
        safeSend(chatId, '🏷️ <b>RENOMMER UNE CIBLE</b>\n\nChoisissez le wallet à renommer :', {
            parse_mode: 'HTML',
            reply_markup: { inline_keyboard: renameButtons }
        });
        return;
    }

    if (data === 'action_blacklist') {
        pendingActions.set(chatId.toString(), { type: 'blacklist' });
        safeSend(chatId, '🚫 <b>BLACKLISTER UN WALLET</b>\n\nEnvoyez l\'adresse du wallet à ignorer :', {
            parse_mode: 'HTML',
            reply_markup: { inline_keyboard: [[{ text: '❌ Annuler', callback_data: 'action_cancel' }]] }
        });
        return;
    }

    if (data === 'action_whitelist') {
        if (blacklist.size === 0) {
            safeSend(chatId, '⚠️ La blacklist est vide.');
            return;
        }
        var wlButtons = [];
        for (const w of blacklist) {
            wlButtons.push([{ text: '✅ ' + shortAddr(w), callback_data: 'wl_' + w }]);
        }
        wlButtons.push([{ text: '❌ Annuler', callback_data: 'action_cancel' }]);
        safeSend(chatId, '✅ <b>RETIRER DE LA BLACKLIST</b>\n\nChoisissez le wallet à retirer :', {
            parse_mode: 'HTML',
            reply_markup: { inline_keyboard: wlButtons }
        });
        return;
    }

    if (data === 'action_cancel') {
        pendingActions.delete(chatId.toString());
        safeSend(chatId, '❌ Action annulée.');
        return;
    }

    if (data === 'action_menu') {
        sendMainMenu(chatId);
        return;
    }

    // --- Suppression par bouton ---
    if (data.startsWith('rm_')) {
        var wallet = data.substring(3);
        if (targetWallets.has(wallet)) {
            var config = targetWallets.get(wallet);
            var labelInfo = config.label ? ' (<b>' + config.label + '</b>)' : '';
            targetWallets.delete(wallet);
            saveDatabase();
            if (activeSubscriptions.has(wallet)) {
                try { solanaConnection.removeOnLogsListener(activeSubscriptions.get(wallet)); } catch (e) {}
                activeSubscriptions.delete(wallet);
            }
            safeSend(chatId, '🗑️ <b>Cible supprimée :</b>\n<code>' + wallet + '</code>' + labelInfo, {
                parse_mode: 'HTML',
                reply_markup: { inline_keyboard: [[{ text: '🔙 Menu', callback_data: 'action_menu' }]] }
            });
        }
        return;
    }

    // --- Renommer par bouton ---
    if (data.startsWith('ren_')) {
        var walletToRename = data.substring(4);
        pendingActions.set(chatId.toString(), { type: 'rename', wallet: walletToRename });
        var currentLabel = targetWallets.get(walletToRename);
        var currentName = currentLabel && currentLabel.label ? currentLabel.label : 'aucun';
        safeSend(chatId, '🏷️ <b>Renommer :</b> <code>' + shortAddr(walletToRename) + '</code>\nNom actuel : <b>' + currentName + '</b>\n\nEnvoyez le nouveau nom :', {
            parse_mode: 'HTML',
            reply_markup: { inline_keyboard: [[{ text: '❌ Annuler', callback_data: 'action_cancel' }]] }
        });
        return;
    }

    // --- Whitelist par bouton ---
    if (data.startsWith('wl_')) {
        var walletToWl = data.substring(3);
        if (blacklist.has(walletToWl)) {
            blacklist.delete(walletToWl);
            saveBlacklist();
            safeSend(chatId, '✅ <b>Wallet retiré de la blacklist :</b>\n<code>' + walletToWl + '</code>', {
                parse_mode: 'HTML',
                reply_markup: { inline_keyboard: [[{ text: '🔙 Menu', callback_data: 'action_menu' }]] }
            });
        }
        return;
    }

    // --- Passer le label lors de l'ajout ---
    if (data === 'add_skip_label') {
        var pendingAdd = pendingActions.get(chatId.toString());
        if (pendingAdd && pendingAdd.type === 'add' && pendingAdd.step === 'label') {
            pendingAdd.label = null;
            finishAdd(chatId, pendingAdd);
        }
        return;
    }
});

// ==========================================
// 💬 GESTION DES MESSAGES TEXTE (saisie interactive)
// ==========================================
bot.on('message', function(msg) {
    if (!isAuthorized(msg)) return;
    if (msg.text && msg.text.startsWith('/')) return; // Ignorer les commandes

    var chatId = msg.chat.id.toString();
    var pending = pendingActions.get(chatId);
    if (!pending) return;

    var text = msg.text ? msg.text.trim() : '';

    // --- AJOUT : étape par étape ---
    if (pending.type === 'add') {
        if (pending.step === 'wallet') {
            try {
                new solanaWeb3.PublicKey(text);
                pending.wallet = text;
                pending.step = 'min';
                pendingActions.set(chatId, pending);
                safeSend(msg.chat.id, '✅ Wallet valide !\n\nEnvoyez le montant <b>minimum</b> (SOL) :', {
                    parse_mode: 'HTML',
                    reply_markup: { inline_keyboard: [[{ text: '❌ Annuler', callback_data: 'action_cancel' }]] }
                });
            } catch (e) {
                safeSend(msg.chat.id, '❌ Adresse invalide. Réessayez :');
            }
            return;
        }

        if (pending.step === 'min') {
            var min = parseFloat(text);
            if (isNaN(min) || min < 0) {
                safeSend(msg.chat.id, '❌ Nombre invalide. Envoyez le minimum en SOL :');
                return;
            }
            pending.min = min;
            pending.step = 'max';
            pendingActions.set(chatId, pending);
            safeSend(msg.chat.id, 'Envoyez le montant <b>maximum</b> (SOL) :', {
                parse_mode: 'HTML',
                reply_markup: { inline_keyboard: [[{ text: '❌ Annuler', callback_data: 'action_cancel' }]] }
            });
            return;
        }

        if (pending.step === 'max') {
            var max = parseFloat(text);
            if (isNaN(max) || max <= pending.min) {
                safeSend(msg.chat.id, '❌ Le max doit être supérieur à ' + pending.min + '. Réessayez :');
                return;
            }
            pending.max = max;
            pending.step = 'label';
            pendingActions.set(chatId, pending);
            safeSend(msg.chat.id, 'Donnez un <b>nom</b> à cette cible (ou appuyez sur Passer) :', {
                parse_mode: 'HTML',
                reply_markup: {
                    inline_keyboard: [
                        [{ text: '⏭️ Passer (sans nom)', callback_data: 'add_skip_label' }],
                        [{ text: '❌ Annuler', callback_data: 'action_cancel' }]
                    ]
                }
            });
            return;
        }

        if (pending.step === 'label') {
            pending.label = text;
            finishAdd(msg.chat.id, pending);
            return;
        }
    }

    // --- BLACKLIST ---
    if (pending.type === 'blacklist') {
        try {
            new solanaWeb3.PublicKey(text);
            if (blacklist.has(text)) {
                safeSend(msg.chat.id, '⚠️ Déjà dans la blacklist.');
                pendingActions.delete(chatId);
                return;
            }
            blacklist.add(text);
            saveBlacklist();
            pendingActions.delete(chatId);
            safeSend(msg.chat.id, '🚫 <b>Wallet blacklisté :</b>\n<code>' + text + '</code>', {
                parse_mode: 'HTML',
                reply_markup: { inline_keyboard: [[{ text: '🔙 Menu', callback_data: 'action_menu' }]] }
            });
        } catch (e) {
            safeSend(msg.chat.id, '❌ Adresse invalide. Réessayez :');
        }
        return;
    }

    // --- RENAME ---
    if (pending.type === 'rename') {
        var walletRen = pending.wallet;
        if (targetWallets.has(walletRen)) {
            var conf = targetWallets.get(walletRen);
            conf.label = text;
            targetWallets.set(walletRen, conf);
            saveDatabase();
            pendingActions.delete(chatId);
            safeSend(msg.chat.id, '🏷️ <b>Wallet renommé :</b>\n<code>' + shortAddr(walletRen) + '</code> → <b>' + text + '</b>', {
                parse_mode: 'HTML',
                reply_markup: { inline_keyboard: [[{ text: '🔙 Menu', callback_data: 'action_menu' }]] }
            });
        }
        return;
    }
});

function finishAdd(chatId, pending) {
    targetWallets.set(pending.wallet, { min: pending.min, max: pending.max, label: pending.label });
    saveDatabase();
    startTracking(pending.wallet);
    pendingActions.delete(chatId.toString());

    var labelLine = pending.label ? '\n🏷️ Label : <b>' + pending.label + '</b>' : '';
    safeSend(chatId,
        '✅ <b>Cible ajoutée !</b>\n\n🕵️ <code>' + pending.wallet + '</code>' + labelLine + '\n🎯 Filtre : <b>' + pending.min + ' à ' + pending.max + ' SOL</b>',
        {
            parse_mode: 'HTML',
            reply_markup: { inline_keyboard: [[{ text: '🔙 Menu', callback_data: 'action_menu' }]] }
        }
    );
}

// ==========================================
// 📱 COMMANDES TEXTE (toujours actives)
// ==========================================

// --- AJOUTER par commande ---
bot.onText(/\/add (\S+) ([\d.]+) ([\d.]+)\s*(.*)/, function(msg, match) {
    if (!isAuthorized(msg)) return;
    var wallet = match[1].trim();
    var min = parseFloat(match[2]);
    var max = parseFloat(match[3]);
    var label = match[4] ? match[4].trim() : null;
    if (!label) label = null;

    if (min >= max) {
        safeSend(TELEGRAM_CHAT_ID, '❌ <b>Erreur :</b> Le min doit être inférieur au max.', { parse_mode: 'HTML' });
        return;
    }
    try {
        new solanaWeb3.PublicKey(wallet);
        targetWallets.set(wallet, { min: min, max: max, label: label });
        saveDatabase();
        startTracking(wallet);
        var labelDisplay = label ? '\n🏷️ Label : <b>' + label + '</b>' : '';
        safeSend(TELEGRAM_CHAT_ID,
            '✅ <b>Cible ajoutée !</b>\n\n🕵️ <code>' + wallet + '</code>' + labelDisplay + '\n🎯 Filtre : <b>' + min + ' à ' + max + ' SOL</b>',
            { parse_mode: 'HTML' }
        );
    } catch (e) {
        safeSend(TELEGRAM_CHAT_ID, '❌ <b>Erreur :</b> Adresse Solana invalide.', { parse_mode: 'HTML' });
    }
});

// --- RENAME par commande ---
bot.onText(/\/rename (\S+) (.+)/, function(msg, match) {
    if (!isAuthorized(msg)) return;
    var wallet = match[1].trim();
    var newLabel = match[2].trim();
    if (targetWallets.has(wallet)) {
        var config = targetWallets.get(wallet);
        config.label = newLabel;
        targetWallets.set(wallet, config);
        saveDatabase();
        safeSend(TELEGRAM_CHAT_ID, '🏷️ <b>Wallet renommé :</b>\n<code>' + wallet + '</code>\n→ <b>' + newLabel + '</b>', { parse_mode: 'HTML' });
    } else {
        safeSend(TELEGRAM_CHAT_ID, '⚠️ Ce wallet n\'est pas dans votre liste.');
    }
});

// --- STATUS par commande ---
bot.onText(/\/status$/, function(msg) {
    if (!isAuthorized(msg)) return;
    sendStatusMessage();
});

function sendStatusMessage() {
    if (targetWallets.size === 0) {
        safeSend(TELEGRAM_CHAT_ID, '📊 Aucun portefeuille surveillé.', {
            reply_markup: { inline_keyboard: [[{ text: '🔙 Menu', callback_data: 'action_menu' }]] }
        });
        return;
    }

    var pauseStatus = isPaused ? '⏸️ EN PAUSE' : '▶️ ACTIF';
    var list = Array.from(targetWallets.entries()).map(function(entry) {
        var w = entry[0];
        var config = entry[1];
        var labelLine = config.label ? '🏷️ <b>' + config.label + '</b>\n' : '';
        return labelLine + '👤 <code>' + w + '</code>\n🎯 [' + config.min + ' - ' + config.max + ' SOL]';
    }).join('\n\n');

    var status = '📊 <b>STATUT DU RADAR</b> — ' + pauseStatus + '\n\n' +
        '🔍 <b>Cibles (' + targetWallets.size + ') :</b>\n\n' + list + '\n\n' +
        '🔗 <b>Wallets dynamiques :</b> ' + walletsUnderSurveillance.size + '\n' +
        '🚫 <b>Blacklistés :</b> ' + blacklist.size;
    safeSend(TELEGRAM_CHAT_ID, status, {
        parse_mode: 'HTML',
        reply_markup: { inline_keyboard: [[{ text: '🔙 Menu', callback_data: 'action_menu' }]] }
    });
}

// --- STATS par commande ---
bot.onText(/\/stats$/, function(msg) {
    if (!isAuthorized(msg)) return;
    sendStatsMessage();
});

function sendStatsMessage() {
    var mem = process.memoryUsage();
    var uptimeSec = process.uptime();
    var hours = Math.floor(uptimeSec / 3600);
    var minutes = Math.floor((uptimeSec % 3600) / 60);

    var stats = '📈 <b>STATISTIQUES</b>\n\n' +
        '⏱️ <b>Uptime :</b> ' + hours + 'h ' + minutes + 'm\n' +
        '🧠 <b>RAM :</b> ' + (mem.heapUsed / 1024 / 1024).toFixed(1) + ' MB\n' +
        '📝 <b>Signatures :</b> ' + processedSignatures.size + '\n' +
        '🎯 <b>Cibles :</b> ' + targetWallets.size + '\n' +
        '🔗 <b>Dynamiques :</b> ' + walletsUnderSurveillance.size + '\n' +
        '🚫 <b>Blacklistés :</b> ' + blacklist.size + '\n' +
        '📡 <b>Souscriptions WS :</b> ' + activeSubscriptions.size + '\n' +
        (isPaused ? '⏸️ Radar en PAUSE' : '▶️ Radar ACTIF');
    safeSend(TELEGRAM_CHAT_ID, stats, {
        parse_mode: 'HTML',
        reply_markup: { inline_keyboard: [[{ text: '🔙 Menu', callback_data: 'action_menu' }]] }
    });
}

// --- REMOVE par commande ---
bot.onText(/\/remove (\S+)/, function(msg, match) {
    if (!isAuthorized(msg)) return;
    var wallet = match[1].trim();
    if (targetWallets.has(wallet)) {
        var config = targetWallets.get(wallet);
        var labelInfo = config.label ? ' (<b>' + config.label + '</b>)' : '';
        targetWallets.delete(wallet);
        saveDatabase();
        if (activeSubscriptions.has(wallet)) {
            try { solanaConnection.removeOnLogsListener(activeSubscriptions.get(wallet)); } catch (e) {}
            activeSubscriptions.delete(wallet);
        }
        safeSend(TELEGRAM_CHAT_ID, '🗑️ <b>Cible supprimée :</b>\n<code>' + wallet + '</code>' + labelInfo, { parse_mode: 'HTML' });
    } else {
        safeSend(TELEGRAM_CHAT_ID, '⚠️ Ce wallet n\'est pas dans votre liste.');
    }
});

// --- PAUSE / RESUME par commande ---
bot.onText(/\/pause$/, function(msg) {
    if (!isAuthorized(msg)) return;
    isPaused = true;
    safeSend(TELEGRAM_CHAT_ID, '⏸️ <b>Radar en pause.</b>', { parse_mode: 'HTML' });
});

bot.onText(/\/resume$/, function(msg) {
    if (!isAuthorized(msg)) return;
    isPaused = false;
    safeSend(TELEGRAM_CHAT_ID, '▶️ <b>Radar repris !</b>', { parse_mode: 'HTML' });
});

// --- CLEAR par commande ---
bot.onText(/\/clear$/, function(msg) {
    if (!isAuthorized(msg)) return;
    var count = processedSignatures.size;
    processedSignatures.clear();
    safeSend(TELEGRAM_CHAT_ID, '🧹 <b>Cache vidé :</b> ' + count + ' signatures.', { parse_mode: 'HTML' });
});

// --- BLACKLIST par commande ---
bot.onText(/\/blacklist (\S+)/, function(msg, match) {
    if (!isAuthorized(msg)) return;
    var wallet = match[1].trim();
    try {
        new solanaWeb3.PublicKey(wallet);
        if (blacklist.has(wallet)) {
            safeSend(TELEGRAM_CHAT_ID, '⚠️ Déjà dans la blacklist.');
            return;
        }
        blacklist.add(wallet);
        saveBlacklist();
        safeSend(TELEGRAM_CHAT_ID, '🚫 <b>Wallet blacklisté :</b>\n<code>' + wallet + '</code>', { parse_mode: 'HTML' });
    } catch (e) {
        safeSend(TELEGRAM_CHAT_ID, '❌ Adresse invalide.', { parse_mode: 'HTML' });
    }
});

bot.onText(/\/whitelist (\S+)/, function(msg, match) {
    if (!isAuthorized(msg)) return;
    var wallet = match[1].trim();
    if (blacklist.has(wallet)) {
        blacklist.delete(wallet);
        saveBlacklist();
        safeSend(TELEGRAM_CHAT_ID, '✅ <b>Retiré de la blacklist :</b>\n<code>' + wallet + '</code>', { parse_mode: 'HTML' });
    } else {
        safeSend(TELEGRAM_CHAT_ID, '⚠️ Pas dans la blacklist.');
    }
});

bot.onText(/\/blacklisted$/, function(msg) {
    if (!isAuthorized(msg)) return;
    sendBlacklistMessage();
});

function sendBlacklistMessage() {
    if (blacklist.size === 0) {
        safeSend(TELEGRAM_CHAT_ID, '🚫 La blacklist est vide.', {
            reply_markup: { inline_keyboard: [[{ text: '🔙 Menu', callback_data: 'action_menu' }]] }
        });
        return;
    }
    var list = Array.from(blacklist).map(function(w) { return '• <code>' + w + '</code>'; }).join('\n');
    safeSend(TELEGRAM_CHAT_ID, '🚫 <b>BLACKLIST (' + blacklist.size + ')</b>\n\n' + list, {
        parse_mode: 'HTML',
        reply_markup: { inline_keyboard: [[{ text: '🔙 Menu', callback_data: 'action_menu' }]] }
    });
}

// ==========================================
// 🧠 LOGIQUE DU RADAR
// ==========================================

async function sendTG(message) {
    try {
        await safeSend(TELEGRAM_CHAT_ID, message, { parse_mode: 'HTML', disable_web_page_preview: true });
    } catch (e) {
        console.error('[X] Erreur envoi Telegram :', e.message);
    }
}

async function extractTokenMint(signature) {
    try {
        await rpcLimiter.wait();
        await new Promise(function(r) { setTimeout(r, TX_FETCH_DELAY); });
        var tx = await solanaConnection.getParsedTransaction(signature, {
            maxSupportedTransactionVersion: 0,
            commitment: 'confirmed',
        });
        if (tx && tx.meta && tx.meta.postTokenBalances) {
            for (var i = 0; i < tx.meta.postTokenBalances.length; i++) {
                var b = tx.meta.postTokenBalances[i];
                if (b.mint !== WSOL_ADDRESS) return b.mint;
            }
        }
    } catch (e) {
        console.error('[X] Erreur extractTokenMint :', e.message);
    }
    return null;
}

async function buildTokenAlert(platform, wallet, ca, signature) {
    var tokenInfo = '';

    if (ca) {
        var meta = await fetchTokenMetadata(ca);
        if (meta.name || meta.symbol) {
            tokenInfo += '\n🪙 <b>Nom :</b> ' + (meta.name || 'Inconnu');
            tokenInfo += '\n🏷️ <b>Symbole :</b> ' + (meta.symbol ? '$' + meta.symbol : 'Inconnu');
        }
        if (meta.supply) {
            tokenInfo += '\n📦 <b>Supply :</b> ' + meta.supply;
        }
    }

    var caDisplay = ca || 'Inconnu_Vérifiez_Solscan';
    var caLinks = ca
        ? '\n\n📈 <a href="https://photon-sol.tinyastro.io/en/lp/' + ca + '">Photon</a> | 📊 <a href="https://solscan.io/tx/' + signature + '">Solscan</a>'
        : '\n\n📊 <a href="https://solscan.io/tx/' + signature + '">Solscan</a>';

    return '🚨 <b>LANCEMENT : ' + platform + '</b>\n\n' +
        '💻 <b>Déployeur:</b> <code>' + wallet + '</code>' + tokenInfo + '\n' +
        '🔥 <b>CA:</b>\n<code>' + caDisplay + '</code>' + caLinks;
}

async function analyzeHop(signature, sourceWallet, originalConfig) {
    if (processedSignatures.has(signature)) return;
    processedSignatures.add(signature);
    try {
        await rpcLimiter.wait();
        var tx = await solanaConnection.getParsedTransaction(signature, {
            maxSupportedTransactionVersion: 0,
            commitment: 'confirmed',
        });
        if (!tx || !tx.transaction || !tx.transaction.message || !tx.transaction.message.instructions) return;

        var instructions = tx.transaction.message.instructions;
        for (var i = 0; i < instructions.length; i++) {
            var ix = instructions[i];
            if (ix.program === 'system' && ix.parsed && ix.parsed.type === 'transfer') {
                var info = ix.parsed.info;
                if (info.source === sourceWallet) {
                    var dest = info.destination;

                    if (blacklist.has(dest)) {
                        console.log('[🚫] Wallet blacklisté ignoré dans hop : ' + shortAddr(dest));
                        continue;
                    }

                    var amount = info.lamports / solanaWeb3.LAMPORTS_PER_SOL;
                    var destIndex = tx.transaction.message.accountKeys.findIndex(function(acc) {
                        return acc.pubkey.toString() === dest;
                    });
                    var minFilter = (originalConfig && originalConfig.min != null) ? originalConfig.min : 2.0;
                    var maxFilter = (originalConfig && originalConfig.max != null) ? originalConfig.max : Infinity;

                    if (destIndex !== -1 && tx.meta && tx.meta.preBalances[destIndex] === 0 &&
                        amount >= minFilter && amount <= maxFilter) {

                        var sourceLabel = (originalConfig && originalConfig.label)
                            ? '<b>' + originalConfig.label + '</b> (<code>' + shortAddr(sourceWallet) + '</code>)'
                            : '<code>' + sourceWallet + '</code>';

                        await sendTG(
                            '🔀 <b>REBOND DÉTECTÉ !</b>\n\n' +
                            '🕵️‍♂️ <b>De :</b> ' + sourceLabel + '\n' +
                            '🎯 <b>Vers :</b> <code>' + dest + '</code>\n' +
                            '💰 <b>Montant :</b> ' + amount.toFixed(4) + ' SOL'
                        );
                        startTrackingNewWallet(dest, originalConfig);
                    }
                }
            }
        }
    } catch (e) {
        console.error('[X] Erreur analyzeHop :', e.message);
    }
}

function startTrackingNewWallet(wallet, originalConfig) {
    if (walletsUnderSurveillance.has(wallet)) return;

    var subId = solanaConnection.onLogs(new solanaWeb3.PublicKey(wallet), async function(logs) {
        if (isPaused) return;
        if (logs.err) return;
        lastLogReceived = Date.now();

        var logStr = logs.logs.join(' ');
        if (logStr.includes(RAYDIUM_PROGRAM_ID) || logStr.includes(PUMPFUN_PROGRAM_ID)) {
            var plat = logStr.includes(RAYDIUM_PROGRAM_ID) ? '🚀 RAYDIUM POOL' : '💊 PUMP.FUN TOKEN';
            var ca = await extractTokenMint(logs.signature);
            var alert = await buildTokenAlert(plat, wallet, ca, logs.signature);
            await sendTG(alert);
        } else {
            analyzeHop(logs.signature, wallet, originalConfig);
        }
    }, 'confirmed');

    walletsUnderSurveillance.set(wallet, { subId: subId, addedAt: Date.now() });
    console.log('[+] Wallet dynamique suivi : ' + shortAddr(wallet));
}

async function analyzeSource(signature, sourceWallet) {
    if (isPaused) return;
    if (processedSignatures.has(signature)) return;
    processedSignatures.add(signature);

    var walletConfig = targetWallets.get(sourceWallet);
    if (!walletConfig) return;

    try {
        await rpcLimiter.wait();
        var tx = await solanaConnection.getParsedTransaction(signature, {
            maxSupportedTransactionVersion: 0,
            commitment: 'confirmed',
        });
        if (!tx || !tx.transaction || !tx.transaction.message || !tx.transaction.message.instructions) return;

        var instructions = tx.transaction.message.instructions;
        for (var i = 0; i < instructions.length; i++) {
            var ix = instructions[i];
            if (ix.program === 'system' && ix.parsed && ix.parsed.type === 'transfer' &&
                ix.parsed.info.source === sourceWallet) {
                var amount = ix.parsed.info.lamports / solanaWeb3.LAMPORTS_PER_SOL;
                var dest = ix.parsed.info.destination;

                if (blacklist.has(dest)) {
                    console.log('[🚫] Wallet blacklisté ignoré : ' + shortAddr(dest));
                    continue;
                }

                var destIndex = tx.transaction.message.accountKeys.findIndex(function(acc) {
                    return acc.pubkey.toString() === dest;
                });

                if (destIndex !== -1 && tx.meta && tx.meta.preBalances[destIndex] === 0) {
                    if (amount >= walletConfig.min && amount <= walletConfig.max) {
                        var sourceDisplay = walletConfig.label
                            ? '<b>' + walletConfig.label + '</b> (<code>' + shortAddr(sourceWallet) + '</code>)'
                            : '<code>' + sourceWallet + '</code>';

                        await sendTG(
                            '🎯 <b>BINGO ! FINANCEMENT DÉTECTÉ</b>\n\n' +
                            '🕵️‍♂️ <b>Cible:</b> ' + sourceDisplay + '\n' +
                            '💰 <b>Montant:</b> ' + amount.toFixed(4) + ' SOL\n' +
                            '🎯 <b>Nouveau Wallet:</b>\n<code>' + dest + '</code>'
                        );
                        startTrackingNewWallet(dest, walletConfig);
                    }
                }
            }
        }
    } catch (e) {
        console.error('[X] Erreur analyzeSource :', e.message);
    }
}

function startTracking(wallet) {
    if (activeSubscriptions.has(wallet)) return;
    var subId = solanaConnection.onLogs(new solanaWeb3.PublicKey(wallet), function(logs) {
        if (!logs.err) {
            lastLogReceived = Date.now();
            analyzeSource(logs.signature, wallet);
        }
    }, 'confirmed');
    activeSubscriptions.set(wallet, subId);
    var config = targetWallets.get(wallet);
    var label = (config && config.label) ? ' (' + config.label + ')' : '';
    console.log('[🎯] Surveillance active : ' + shortAddr(wallet) + label);
}

// ==========================================
// 🛑 ARRÊT PROPRE
// ==========================================
async function shutdown(signal) {
    console.log('\n[🛑] Signal ' + signal + ' reçu. Arrêt propre...');
    await sendTG('🔴 <b>Radar arrêté.</b>');

    for (const [wallet, subId] of activeSubscriptions.entries()) {
        try { solanaConnection.removeOnLogsListener(subId); } catch (e) {}
    }
    for (const [wallet, info] of walletsUnderSurveillance.entries()) {
        try { solanaConnection.removeOnLogsListener(info.subId); } catch (e) {}
    }

    bot.stopPolling();
    console.log('[🛑] Bot arrêté proprement.');
    process.exit(0);
}

process.on('SIGINT', function() { shutdown('SIGINT'); });
process.on('SIGTERM', function() { shutdown('SIGTERM'); });

// ==========================================
// 🚀 LANCEMENT
// ==========================================
console.log('[⚡] RADAR V12 PRÊT — ' + targetWallets.size + ' cible(s), ' + blacklist.size + ' blacklisté(s)');
targetWallets.forEach(function(config, wallet) { startTracking(wallet); });
sendTG(
    '🟢 <b>Radar V12 Activé !</b>\n' +
    '🎯 <code>' + targetWallets.size + '</code> cible(s)\n' +
    '🚫 <code>' + blacklist.size + '</code> blacklisté(s)\n\n' +
    'Tapez /menu pour ouvrir le panneau de contrôle.'
);
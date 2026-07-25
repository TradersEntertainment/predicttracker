import React, { useState, useEffect } from 'react';
import './index.css';

interface Whale {
  id: string;
  address: string;
  name: string;
  addedAt: string;
  status: 'tracking' | 'paused';
  chat_id?: string;
}

interface BalanceInfo {
  usdc_balance: number;
  portfolio_value: number;
  last_updated: number;
  nickname: string;
}

interface ScannerStatus {
  status: string;
  last_scan_time: number;
  seconds_since_last_scan: number;
  total_scans_count: number;
  last_trade_info?: string;
}


interface LimitlessWallet {
  id: number;
  address: string;
  name: string;
  chat_id?: string;
  added_at: string;
  status: string;
}

interface OrderbookMonitor {
  id: string;
  name: string;
  market_id?: string;
  min_shares: number;
  chat_id?: string;
  status: string;
  created_at: string;
}

function App() {
  const [whales, setWhales] = useState<Whale[]>([]);
  const [balances, setBalances] = useState<Record<string, BalanceInfo>>({});
  const [scannerInfo, setScannerInfo] = useState<ScannerStatus | null>(null);
  const [orderbookMonitors, setOrderbookMonitors] = useState<OrderbookMonitor[]>([]);
  
  // Whale Form
  const [address, setAddress] = useState('');
  const [name, setName] = useState('');
  const [chatId, setChatId] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  
  // Orderbook Form
  const [obName, setObName] = useState('Bitcoin 5M Likidite Duvarı');
  const [obMinShares, setObMinShares] = useState('2000');
  const [obMarketId, setObMarketId] = useState('');
  const [obChatId, setObChatId] = useState('');

  
  const [limitlessWallets, setLimitlessWallets] = useState<LimitlessWallet[]>([]);
  const [limitlessAddress, setLimitlessAddress] = useState('');
  const [limitlessName, setLimitlessName] = useState('');
  const [limitlessChatId, setLimitlessChatId] = useState('');

  const [toast, setToast] = useState<{ message: string; visible: boolean }>({ message: '', visible: false });

  let rawApiBase = (import.meta.env.VITE_API_URL || '').trim();
  if (rawApiBase && !rawApiBase.startsWith('http://') && !rawApiBase.startsWith('https://')) {
    rawApiBase = `https://${rawApiBase}`;
  }
  const API_BASE = rawApiBase;

  const fetchWhales = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/whales`);
      const data = await response.json();
      setWhales(data.whales || []);
    } catch (e) {
      console.error('Error fetching whales', e);
    }
  };

  const fetchBalances = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/balances`);
      const data = await response.json();
      setBalances(data.balances || {});
    } catch (e) {
      console.error('Error fetching balances', e);
    }
  };

  const fetchScannerStatus = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/scanner_status`);
      const data = await response.json();
      setScannerInfo(data);
    } catch (e) {
      console.error('Error fetching scanner status', e);
    }
  };

  
  const fetchLimitlessWallets = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/limitless_wallets`);
      const data = await response.json();
      setLimitlessWallets(data || []);
    } catch (e) {
      console.error('Error fetching limitless wallets', e);
    }
  };

  const fetchOrderbookMonitors = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/orderbook_monitors`);
      const data = await response.json();
      setOrderbookMonitors(data.monitors || []);
    } catch (e) {
      console.error('Error fetching orderbook monitors', e);
    }
  };

  useEffect(() => {
    fetchWhales();
    fetchBalances();
    fetchScannerStatus();
    fetchOrderbookMonitors();
    fetchLimitlessWallets();

    const whaleInterval = setInterval(fetchWhales, 10000);
    const balanceInterval = setInterval(fetchBalances, 30000);
    const scannerInterval = setInterval(fetchScannerStatus, 5000);
    const obInterval = setInterval(fetchOrderbookMonitors, 10000);
    const limitlessInterval = setInterval(fetchLimitlessWallets, 5000);

    return () => {
      clearInterval(whaleInterval);
      clearInterval(balanceInterval);
      clearInterval(scannerInterval);
      clearInterval(obInterval);
      clearInterval(limitlessInterval);
    };
  }, []);

  const showToast = (message: string) => {
    setToast({ message, visible: true });
    setTimeout(() => setToast({ message: '', visible: false }), 4000);
  };

  const handleAddWhale = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!address || !name) {
      showToast('Lütfen tüm alanları doldurun!');
      return;
    }

    if (!address.startsWith('0x') || address.length !== 42) {
      showToast('Geçerli bir cüzdan adresi girin (0x...)');
      return;
    }

    try {
      const response = await fetch(`${API_BASE}/api/whales`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address, name, chat_id: chatId || null })
      });
      
      if (response.ok) {
        setAddress('');
        setName('');
        setChatId('');
        showToast(`${name} başarıyla eklendi!`);
        fetchWhales();
      } else {
        const error = await response.json();
        showToast(`Hata: ${error.detail || 'Eklenemedi'}`);
      }
    } catch (e) {
      showToast('Bağlantı hatası!');
    }
  };

  
  const handleAddLimitlessWallet = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!limitlessAddress || !limitlessName) return;
    try {
      const res = await fetch(`${API_BASE}/api/limitless_wallets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address: limitlessAddress, name: limitlessName, chat_id: limitlessChatId || null }),
      });
      if (res.ok) {
        setLimitlessAddress('');
        setLimitlessName('');
        setLimitlessChatId('');
        fetchLimitlessWallets();
        showToast('🌀 Limitless Balina takibe alındı!');
      } else {
        showToast('❌ Hata oluştu.');
      }
    } catch (err) {
      showToast('❌ Bağlantı hatası.');
    }
  };

  const handleDeleteLimitlessWallet = async (addr: string) => {
    try {
      await fetch(`${API_BASE}/api/limitless_wallets/${addr}`, { method: 'DELETE' });
      fetchLimitlessWallets();
      showToast('🗑️ Limitless balinası silindi.');
    } catch (err) {
      showToast('❌ Hata oluştu.');
    }
  };

  const handleTestLimitlessWallet = async (addr: string) => {
    try {
      showToast('🧪 Limitless test bildirimi gönderiliyor...');
      const res = await fetch(`${API_BASE}/api/limitless_wallets/${addr}/test`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        showToast("✅ Test bildirimi Telegram'a düştü!");
      } else {
        showToast(`❌ ${data.detail || 'Test başarısız'}`);
      }
    } catch (err) {
      showToast('❌ Test gönderilemedi.');
    }
  };

  const handleAddOrderbookMonitor = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!obName || !obMinShares) {
      showToast('Lütfen etiket ve min shares miktarını doldurun!');
      return;
    }

    try {
      const response = await fetch(`${API_BASE}/api/orderbook_monitors`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: obName,
          market_id: obMarketId || null,
          min_shares: parseFloat(obMinShares) || 2000,
          chat_id: obChatId || null
        })
      });

      if (response.ok) {
        setObMarketId('');
        showToast('Orderbook likidite takibi başlatıldı! 🧱');
        fetchOrderbookMonitors();
    fetchLimitlessWallets();
      } else {
        const error = await response.json();
        showToast(`Hata: ${error.detail || 'Eklenemedi'}`);
      }
    } catch (e) {
      showToast('Bağlantı hatası!');
    }
  };

  const handleDeleteOrderbookMonitor = async (id: string) => {
    try {
      const response = await fetch(`${API_BASE}/api/orderbook_monitors/${id}`, { method: 'DELETE' });
      if (response.ok) {
        showToast('Orderbook takibi kaldırıldı.');
        fetchOrderbookMonitors();
    fetchLimitlessWallets();
      }
    } catch (e) {
      showToast('Hata oluştu!');
    }
  };

  const handleTestOrderbookTelegram = async () => {
    try {
      showToast('Orderbook test bildirimi gönderiliyor... 🧪');
      const response = await fetch(`${API_BASE}/api/test_orderbook_telegram`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: obChatId || null, min_shares: parseFloat(obMinShares) || 2000 })
      });
      if (response.ok) {
        showToast('✅ Orderbook test bildirimi gönderildi! Telegramınızı kontrol edin.');
      } else {
        const error = await response.json();
        showToast(`⚠️ Hata: ${error.detail || 'Bildirim gönderilemedi'}`);
      }
    } catch (e) {
      showToast('⚠️ Bağlantı hatası!');
    }
  };

  const handleRemove = async (addressToRemove: string, name: string) => {
    const confirmed = window.confirm(`"${name}" isimli balinanın takibini durdurmak istediğinize emin misiniz?`);
    if (!confirmed) return;
    try {
      const response = await fetch(`${API_BASE}/api/whales/${addressToRemove}`, { method: 'DELETE' });
      if (response.ok) {
        showToast('Balina takibi durduruldu.');
        fetchWhales();
      }
    } catch (e) {
      showToast('Hata oluştu!');
    }
  };

  const handleReactivate = async (addressToReactivate: string, name: string) => {
    try {
      const response = await fetch(`${API_BASE}/api/whales/${addressToReactivate}/reactivate`, { method: 'POST' });
      if (response.ok) {
        showToast(`"${name}" yeniden aktif takibe alındı! 🐋`);
        fetchWhales();
      }
    } catch (e) {
      showToast('Hata oluştu!');
    }
  };

  const handleRemovePermanent = async (addressToRemove: string, name: string) => {
    const confirmed = window.confirm(`"${name}" isimli balinayı TAMAMEN silmek istediğinize emin misiniz?`);
    if (!confirmed) return;
    try {
      const response = await fetch(`${API_BASE}/api/whales/${addressToRemove}/permanent`, { method: 'DELETE' });
      if (response.ok) {
        showToast('Balina tamamen silindi.');
        fetchWhales();
      }
    } catch (e) {
      showToast('Hata oluştu!');
    }
  };

  const handleTestTelegram = async (addressToTest?: string) => {
    try {
      showToast('Telegram test bildirimi gönderiliyor... 🧪');
      const url = addressToTest 
        ? `${API_BASE}/api/whales/${addressToTest}/test`
        : `${API_BASE}/api/test_telegram`;
        
      const response = await fetch(url, { method: 'POST' });
      if (response.ok) {
        showToast('✅ Telegram test bildirimi başarıyla gönderildi! Telegramınızı kontrol edin.');
      } else {
        const error = await response.json();
        showToast(`⚠️ Hata: ${error.detail || 'Bildirim gönderilemedi'}`);
      }
    } catch (e) {
      showToast('⚠️ Bağlantı hatası! Telegram bildirimi gönderilemedi.');
    }
  };

  const truncateAddress = (addr: string): string => {
    if (!addr) return '';
    return `${addr.substring(0, 6)}...${addr.substring(addr.length - 4)}`;
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    showToast('Cüzdan adresi kopyalandı! 📋');
  };

  const formatBalance = (value: number): string => {
    if (value >= 1000000) return `$${(value / 1000000).toFixed(1)}M`;
    if (value >= 1000) return `$${(value / 1000).toFixed(1)}K`;
    return `$${value.toFixed(2)}`;
  };

  const getBalanceClass = (usdt: number, portfolio: number): string => {
    const total = usdt + portfolio;
    if (total < 1000) return 'balance-danger';
    if (total < 5000) return 'balance-warning';
    return 'balance-ok';
  };

  const activeWhalesList = whales.filter(w => w.status !== 'paused');
  const pausedWhales = whales.filter(w => w.status === 'paused');

  const activeBalances = Object.entries(balances)
    .filter(([addr]) => whales.some(w => w.address.toLowerCase() === addr.toLowerCase() && w.status !== 'paused'))
    .map(([, b]) => b);

  const totalUSDC = activeBalances.reduce((sum, b) => sum + (b.usdc_balance || 0), 0);
  const totalPortfolio = activeBalances.reduce((sum, b) => sum + (b.portfolio_value || 0), 0);
  const lowBalanceCount = activeBalances.filter(b => ((b.usdc_balance || 0) + (b.portfolio_value || 0)) < 1000).length;

  const filteredActive = activeWhalesList.filter(w => 
    w.name.toLowerCase().includes(searchTerm.toLowerCase()) || 
    w.address.toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="container">
      <header className="header">
        <h1 className="gradient-text">Predict Whale & Orderbook Tracker</h1>
        <p>Predict.fun balinalarını ve Orderbook likidite duvarlarını (2000+ Shares) canlı takip edin</p>
        
        {/* Scanner Live Status Bar */}
        <div style={{ marginTop: '15px', display: 'flex', justifyContent: 'center', gap: '15px', alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ background: 'rgba(0, 255, 136, 0.1)', border: '1px solid rgba(0, 255, 136, 0.3)', color: '#00ff88', padding: '6px 16px', borderRadius: '20px', fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#00ff88', boxShadow: '0 0 10px #00ff88' }}></span>
            ⚡ Ultra-Fast Canlı Tarayıcı Aktif (1s) {scannerInfo && scannerInfo.seconds_since_last_scan >= 0 ? `(Son tarama: ${scannerInfo.seconds_since_last_scan}s önce | ${scannerInfo.total_scans_count} tarama)` : ''}
          </div>
          <button 
            onClick={() => handleTestTelegram()} 
            className="btn btn-primary"
            style={{ padding: '8px 18px', fontSize: '0.85rem' }}
          >
            Balina Telegram Testi 🧪
          </button>
        </div>
      </header>

      {/* Stats Bar */}
      {Object.keys(balances).length > 0 && (
        <div className="stats-bar">
          <div className="stat-item">
            <span className="stat-label">Toplam Bakiye (USDT/USDC)</span>
            <span className="stat-value cyan">{formatBalance(totalUSDC)}</span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Toplam Portfolio</span>
            <span className="stat-value purple">{formatBalance(totalPortfolio)}</span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Takip Edilen</span>
            <span className="stat-value">{activeWhalesList.length} 🐋</span>
          </div>
          {lowBalanceCount > 0 && (
            <div className="stat-item warning">
              <span className="stat-label">Düşük Bakiye</span>
              <span className="stat-value red">{lowBalanceCount} ⚠️</span>
            </div>
          )}
        </div>
      )}

      {/* Orderbook Liquidity Wall Section */}
      <div className="glass-panel" style={{ marginBottom: '30px', border: '1px solid rgba(0, 240, 255, 0.3)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
          <h2>🧱 Orderbook / Likidite Duvarı Takibi (2000+ Shares)</h2>
          <button 
            onClick={handleTestOrderbookTelegram} 
            className="btn btn-sm"
            style={{ background: 'rgba(0, 240, 255, 0.15)', color: '#00f0ff', border: '1px solid rgba(0, 240, 255, 0.3)' }}
          >
            Orderbook Telegram Testi Gönder 🧪
          </button>
        </div>
        
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: '5px' }}>
          ✨ 24/7 Otomatik Orderbook Taraması Aktif! Hiçbir butona basmanıza gerek yoktur. Sistem arka planda tüm 5M ve canlı marketlerdeki 2000+ shares alım/satım duvarlarını ($0.05 - $0.95 aktif aralığında) otomatik tarayıp bildirim atar.
        </p>

        <form onSubmit={handleAddOrderbookMonitor} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '15px', marginTop: '20px' }}>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Takip İsmi / Etiket</label>
            <input
              type="text"
              className="input-field"
              placeholder="Örn: Bitcoin 5M Likidite Duvarı"
              value={obName}
              onChange={(e) => setObName(e.target.value)}
            />
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Min Shares Eşiği (Adet)</label>
            <input
              type="number"
              className="input-field"
              placeholder="Örn: 2000"
              value={obMinShares}
              onChange={(e) => setObMinShares(e.target.value)}
            />
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Telegram Chat ID (Bu Grup İçin Özel)</label>
            <input
              type="text"
              className="input-field"
              placeholder="Örn: -100123456789"
              value={obChatId}
              onChange={(e) => setObChatId(e.target.value)}
            />
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Market ID veya Link (Opsiyonel)</label>
            <input
              type="text"
              className="input-field"
              placeholder="Boş bırakılırsa TÜM marketleri tarar"
              value={obMarketId}
              onChange={(e) => setObMarketId(e.target.value)}
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end' }}>
            <button type="submit" className="btn btn-primary" style={{ width: '100%', height: '42px' }}>
              Özel Takip Ekle 🧱
            </button>
          </div>
        </form>

        {/* Active Orderbook Monitors List */}
        {orderbookMonitors.length > 0 && (
          <div style={{ marginTop: '20px' }}>
            <h4 style={{ color: 'var(--text-secondary)', marginBottom: '10px' }}>Aktif Orderbook Takipleri ({orderbookMonitors.length})</h4>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px' }}>
              {orderbookMonitors.map((m) => (
                <div key={m.id} style={{ background: 'rgba(255, 255, 255, 0.05)', padding: '10px 15px', borderRadius: '10px', display: 'flex', alignItems: 'center', gap: '15px', border: '1px solid rgba(255, 255, 255, 0.1)' }}>
                  <div>
                    <strong style={{ color: '#00f0ff', fontSize: '0.9rem' }}>{m.name}</strong>
                    <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                      Eşik: <span style={{ color: '#00ff88' }}>{m.min_shares}+ Shares</span>
                      {m.chat_id ? ` | Chat ID: ${m.chat_id}` : ' | Varsayılan Telegram'}
                    </div>
                  </div>
                  <button onClick={() => handleDeleteOrderbookMonitor(m.id)} className="btn btn-danger btn-sm" style={{ padding: '4px 10px' }}>Sil 🗑️</button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      
      {/* 🌀 LIMITLESS EXCHANGE BALİNA TAKİBİ */}
      <div className="glass-panel" style={{ marginBottom: '30px', background: 'linear-gradient(135deg, rgba(147, 51, 234, 0.1), rgba(79, 70, 229, 0.05))', border: '1px solid rgba(168, 85, 247, 0.3)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
          <h2 style={{ color: '#c084fc' }}>🌀 Limitless Exchange Balına Takibi (Base Network)</h2>
          <span style={{ fontSize: '0.8rem', background: 'rgba(168, 85, 247, 0.2)', color: '#e9d5ff', padding: '4px 12px', borderRadius: '20px', border: '1px solid rgba(168, 85, 247, 0.4)' }}>
            ⚡ 24/7 Otomatik Base RPC & Explorer Tarayıcısı
          </span>
        </div>
        
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: '5px' }}>
          Limitless Exchange (limitless.exchange) platformundaki balina cüzdanlarının alım, satım ve pozisyon kapatma işlemlerini anında Telegram kanalınıza düşürür.
        </p>

        <form onSubmit={handleAddLimitlessWallet} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '15px', marginTop: '20px' }}>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Limitless Cüzdan Adresi</label>
            <input
              type="text"
              className="input-field"
              placeholder="0x328c4072920e5e3f95911e887c077c23deb91901"
              value={limitlessAddress}
              onChange={(e) => setLimitlessAddress(e.target.value)}
            />
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Balına İsmi / Etiket</label>
            <input
              type="text"
              className="input-field"
              placeholder="Örn: Limitless Balina 1"
              value={limitlessName}
              onChange={(e) => setLimitlessName(e.target.value)}
            />
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Telegram Chat ID (Opsiyonel)</label>
            <input
              type="text"
              className="input-field"
              placeholder="Boş bırakılırsa ana Telegram kanalı"
              value={limitlessChatId}
              onChange={(e) => setLimitlessChatId(e.target.value)}
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end' }}>
            <button type="submit" className="btn btn-primary" style={{ width: '100%', height: '42px', background: 'linear-gradient(135deg, #a855f7, #7e22ce)' }}>
              Limitless Balina Ekle 🌀
            </button>
          </div>
        </form>

        {limitlessWallets.length > 0 && (
          <div style={{ marginTop: '20px' }}>
            <h4 style={{ color: 'var(--text-secondary)', marginBottom: '10px' }}>Takip Edilen Limitless Balinaları ({limitlessWallets.length})</h4>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px' }}>
              {limitlessWallets.map((w) => (
                <div key={w.address} style={{ background: 'rgba(255, 255, 255, 0.05)', padding: '10px 15px', borderRadius: '10px', display: 'flex', alignItems: 'center', gap: '15px', border: '1px solid rgba(168, 85, 247, 0.3)' }}>
                  <div>
                    <strong style={{ color: '#c084fc', fontSize: '0.9rem' }}>🌀 {w.name}</strong>
                    <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
                      {w.address.slice(0, 8)}...{w.address.slice(-6)}
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button onClick={() => handleTestLimitlessWallet(w.address)} className="btn btn-sm" style={{ background: 'rgba(168, 85, 247, 0.2)', color: '#c084fc', border: '1px solid #a855f7', padding: '4px 10px' }}>Test 🧪</button>
                    <button onClick={() => handleDeleteLimitlessWallet(w.address)} className="btn btn-danger btn-sm" style={{ padding: '4px 10px' }}>Sil 🗑️</button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="dashboard-grid">
        {/* Left Column: Form */}
        <div className="glass-panel">
          <h2>Yeni Balina Ekle</h2>
          <form onSubmit={handleAddWhale} style={{ marginTop: '20px' }}>
            <div className="form-group">
              <label>Cüzdan Adresi</label>
              <input
                type="text"
                className="input-field"
                placeholder="0x17C99..."
                value={address}
                onChange={(e) => setAddress(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Balina İsmi / Etiket</label>
              <input
                type="text"
                className="input-field"
                placeholder="Örn: Big Trader"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Telegram Chat ID (Opsiyonel)</label>
              <input
                type="text"
                className="input-field"
                placeholder="Örn: -100123456789"
                value={chatId}
                onChange={(e) => setChatId(e.target.value)}
              />
            </div>
            <button type="submit" className="btn btn-primary" style={{ width: '100%' }}>
              Balinayı Takibe Al 🚀
            </button>
          </form>

          <div style={{ marginTop: '20px', padding: '12px', background: 'rgba(255, 255, 255, 0.03)', borderRadius: '10px', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
            💡 <b>Bilgi:</b> Sistem her 1 saniyede bir (Ultra-Fast) eşzamanlı olarak Predict.fun GraphQL altyapısını taramaktadır. Balina yeni bir alım veya satım yaptığı anda otomatik bildirim Telegram'a düşer.
          </div>
        </div>

        {/* Right Column: List & Balances */}
        <div className="glass-panel">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
            <h2>Takip Edilen Balinalar ({filteredActive.length})</h2>
            <input
              type="text"
              className="input-field"
              placeholder="Ara (İsim veya Adres)..."
              style={{ width: '250px', padding: '8px 14px' }}
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>

          <div className="whale-list">
            {filteredActive.length === 0 ? (
              <p style={{ color: 'var(--text-secondary)', textAlign: 'center', padding: '30px' }}>
                Takip edilen balina bulunamadı.
              </p>
            ) : (
              filteredActive.map((w) => {
                const bal = balances[w.address.toLowerCase()] || balances[w.address];
                const usdt = bal?.usdc_balance || 0;
                const port = bal?.portfolio_value || 0;
                const total = usdt + port;
                const balClass = getBalanceClass(usdt, port);

                return (
                  <div key={w.address} className="whale-card">
                    <div className="whale-info">
                      <div className="whale-header">
                        <span className="whale-name">{w.name}</span>
                        <span className="badge badge-success">Takipte</span>
                      </div>
                      <div className="whale-address" onClick={() => copyToClipboard(w.address)}>
                        {truncateAddress(w.address)} 📋
                      </div>
                    </div>

                    <div className="balance-box">
                      <div className={`balance-badge ${balClass}`}>
                        <span className="balance-title">Toplam</span>
                        <span className="balance-amount">{formatBalance(total)}</span>
                      </div>
                      <div className="balance-details">
                        <span>Cüzdan: {formatBalance(usdt)}</span>
                        <span>Portfolio: {formatBalance(port)}</span>
                      </div>
                    </div>

                    <div className="whale-actions" style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                      <a
                        href={`https://predict.fun/portfolio/${w.address}`}
                        target="_blank"
                        rel="noreferrer"
                        className="btn-icon"
                        title="Predict.fun Profili"
                      >
                        🎯
                      </a>
                      <a
                        href={`https://bscscan.com/address/${w.address}`}
                        target="_blank"
                        rel="noreferrer"
                        className="btn-icon"
                        title="BscScan Explorer"
                      >
                        🔍
                      </a>
                      <button
                        onClick={() => handleTestTelegram(w.address)}
                        className="btn btn-sm"
                        style={{ background: 'rgba(0, 240, 255, 0.15)', color: '#00f0ff', border: '1px solid rgba(0, 240, 255, 0.3)' }}
                        title="Telegram Test Bildirimi Gönder"
                      >
                        Test 🧪
                      </button>
                      <button
                        onClick={() => handleRemove(w.address, w.name)}
                        className="btn btn-danger btn-sm"
                        title="Takibi Durdur"
                      >
                        Duraklat
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* Paused Section */}
          {pausedWhales.length > 0 && (
            <div style={{ marginTop: '40px' }}>
              <h3 style={{ color: 'var(--text-secondary)', marginBottom: '15px' }}>
                Duraklatılmış Balinalar ({pausedWhales.length})
              </h3>
              <div className="whale-list">
                {pausedWhales.map((w) => (
                  <div key={w.address} className="whale-card paused">
                    <div className="whale-info">
                      <span className="whale-name">{w.name}</span>
                      <span className="whale-address">{truncateAddress(w.address)}</span>
                    </div>
                    <div className="whale-actions">
                      <button
                        onClick={() => handleReactivate(w.address, w.name)}
                        className="btn btn-primary btn-sm"
                      >
                        Tekrar Takibe Al 🔄
                      </button>
                      <button
                        onClick={() => handleRemovePermanent(w.address, w.name)}
                        className="btn btn-danger btn-sm"
                      >
                        Sil 🗑️
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Toast notification */}
      {toast.visible && <div className="toast-notification">{toast.message}</div>}
    </div>
  );
}

export default App;

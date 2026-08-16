'use client';

import { useState, useEffect } from 'react';
import { MapContainer, TileLayer, Marker, useMapEvents, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { Search, Loader2, Maximize2, Minimize2, X } from 'lucide-react';

const getIcon = () => {
  if (typeof window === 'undefined') return null as any;
  return L.icon({
    iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
    shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
    iconSize: [25, 41],
    iconAnchor: [12, 41],
    popupAnchor: [1, -34],
  });
};

interface MiniMapPickerProps {
  lat: number | null;
  lng: number | null;
  onLocationSelect: (lat: number, lng: number) => void;
}

// Controller component to programmatically fly to location
function MapFlyController({ center, zoom }: { center: [number, number]; zoom: number }) {
  const map = useMap();
  useEffect(() => {
    if (center && center[0] !== 0 && center[1] !== 0) {
      map.flyTo(center, zoom, { duration: 1.2 });
    }
  }, [center[0], center[1], zoom, map]);
  return null;
}

function MapEventsHandler({ onLocationSelect }: { onLocationSelect: (lat: number, lng: number) => void }) {
  useMapEvents({
    click(e) {
      onLocationSelect(e.latlng.lat, e.latlng.lng);
    },
  });
  return null;
}

export default function MiniMapPicker({ lat, lng, onLocationSelect }: MiniMapPickerProps) {
  const [mounted, setMounted] = useState(false);
  const [mapStyle, setMapStyle] = useState<'light' | 'dark' | 'satellite'>('light');
  const [searchQuery, setSearchQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [showResults, setShowResults] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const hasLocation = Number.isFinite(lat) && Number.isFinite(lng);
  const mapCenter: [number, number] = hasLocation 
    ? [lat as number, lng as number] 
    : [16.047079, 108.206230]; // Mặc định trung tâm Việt Nam (Đà Nẵng)
  const mapZoom = hasLocation ? 15 : 6;

  const tileUrls = {
    light: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    dark: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    satellite: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  };

  // Search geocoding via Nominatim
  const handleSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!searchQuery.trim()) return;

    setSearching(true);
    setSearchResults([]);
    try {
      const q = encodeURIComponent(searchQuery.trim());
      const res = await fetch(`https://nominatim.openstreetmap.org/search?format=json&q=${q}&countrycodes=vn&limit=5`);
      const data = await res.json();
      setSearchResults(data || []);
      setShowResults(true);
    } catch (err) {
      console.error('Geocoding search failed:', err);
    } finally {
      setSearching(false);
    }
  };

  const selectSearchResult = (item: any) => {
    const itemLat = parseFloat(item.lat);
    const itemLng = parseFloat(item.lon);
    if (!isNaN(itemLat) && !isNaN(itemLng)) {
      onLocationSelect(itemLat, itemLng);
      setShowResults(false);
      setSearchQuery(item.display_name.split(',')[0]);
    }
  };

  if (!mounted) {
    return <div className="w-full h-full bg-slate-100 animate-pulse rounded-xl" />;
  }

  const renderMapContent = (isFull: boolean) => (
    <div className={`w-full h-full relative z-0 ${isFull ? '' : 'group'}`}>
      {/* Search Input Bar Overlay */}
      <div className="absolute top-2 left-2 right-12 z-[1000] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <form onSubmit={handleSearch} className="flex items-center bg-white/95 backdrop-blur-md rounded-xl border border-slate-300 shadow-md p-1 pl-3 text-xs gap-1.5">
          <Search className="w-3.5 h-3.5 text-slate-400 shrink-0" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              if (!e.target.value) setShowResults(false);
            }}
            placeholder="Gõ tên địa điểm (VD: Nguyễn Trãi, Hà Nội...)"
            className="flex-1 bg-transparent text-slate-800 placeholder-slate-400 outline-none text-xs"
          />
          {searching && <Loader2 className="w-3.5 h-3.5 text-blue-600 animate-spin shrink-0" />}
          <button
            type="button"
            onClick={handleSearch}
            className="px-2.5 py-1 bg-blue-600 hover:bg-blue-700 text-white font-bold rounded-lg text-[10px] uppercase tracking-wider transition-colors shrink-0"
          >
            Tìm
          </button>
        </form>

        {/* Dropdown Suggestions */}
        {showResults && searchResults.length > 0 && (
          <div className="mt-1 bg-white/95 backdrop-blur-md border border-slate-200 rounded-xl shadow-xl max-h-48 overflow-y-auto divide-y divide-slate-100 z-[1001]">
            {searchResults.map((item, index) => (
              <div
                key={index}
                onClick={(e) => {
                  e.stopPropagation();
                  selectSearchResult(item);
                }}
                className="p-2 px-3 text-[11px] text-slate-700 hover:bg-blue-50 cursor-pointer font-medium truncate"
              >
                📍 {item.display_name}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Fullscreen Toggle Button (Top Right) */}
      <button
        type="button"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setIsFullscreen(!isFullscreen);
        }}
        className="absolute top-2 right-2 z-[1000] p-2 bg-white/90 backdrop-blur-md hover:bg-white text-slate-700 rounded-xl border border-slate-300 shadow-md transition-all active:scale-95 flex items-center justify-center"
        title={isFullscreen ? "Thu nhỏ bản đồ" : "Phóng to toàn màn hình"}
      >
        {isFullscreen ? <Minimize2 className="w-4 h-4 text-blue-600" /> : <Maximize2 className="w-4 h-4 text-blue-600" />}
      </button>

      {/* Map Tile Switcher (Bottom Right) */}
      <div className="absolute bottom-2 right-2 z-[1000] bg-white/90 backdrop-blur-md border border-slate-300 rounded-lg p-1 flex items-center gap-1 shadow-md text-[10px]" onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setMapStyle('light'); }}
          className={`px-2 py-0.5 font-bold rounded ${mapStyle === 'light' ? 'bg-blue-600 text-white shadow-sm' : 'text-slate-600 hover:bg-slate-100'}`}
        >
          ☀️ Sáng
        </button>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setMapStyle('dark'); }}
          className={`px-2 py-0.5 font-bold rounded ${mapStyle === 'dark' ? 'bg-blue-600 text-white shadow-sm' : 'text-slate-600 hover:bg-slate-100'}`}
        >
          🌙 Tối
        </button>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setMapStyle('satellite'); }}
          className={`px-2 py-0.5 font-bold rounded ${mapStyle === 'satellite' ? 'bg-blue-600 text-white shadow-sm' : 'text-slate-600 hover:bg-slate-100'}`}
        >
          🛰️ Vệ tinh
        </button>
      </div>

      <MapContainer
        center={mapCenter}
        zoom={mapZoom}
        style={{ height: '100%', width: '100%' }}
        scrollWheelZoom={true}
        zoomControl={true}
      >
        <TileLayer
          key={mapStyle}
          attribution='&copy; OpenStreetMap'
          url={tileUrls[mapStyle]}
        />
        {mapStyle === 'satellite' && (
          <TileLayer url="https://{s}.basemaps.cartocdn.com/rastertiles/voyager_only_labels/{z}/{x}/{y}{r}.png" />
        )}
        {hasLocation && <Marker position={[lat as number, lng as number]} icon={getIcon()} />}
        <MapFlyController center={mapCenter} zoom={mapZoom} />
        <MapEventsHandler onLocationSelect={onLocationSelect} />
      </MapContainer>
    </div>
  );

  return (
    <>
      {renderMapContent(false)}

      {/* Fullscreen Overlay Popup Modal */}
      {isFullscreen && (
        <div 
          className="fixed inset-0 z-[3000] bg-slate-900/80 backdrop-blur-md p-4 sm:p-8 flex flex-col animate-in fade-in zoom-in-95 duration-200"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
          }}
        >
          <div className="flex items-center justify-between bg-white border border-slate-200 px-4 py-3 rounded-t-2xl shadow-sm" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-2">
              <span className="text-sm font-bold text-slate-800">🗺️ BẢN ĐỒ ĐỊNH VỊ TỌA ĐỘ SỰ CỐ (FULLSCREEN)</span>
              <span className="text-xs text-blue-600 font-semibold bg-blue-50 px-2 py-0.5 rounded-full border border-blue-100">
                {hasLocation ? `Tọa độ: ${lat?.toFixed(5)}, ${lng?.toFixed(5)}` : 'Nhấp chuột trên bản đồ để chọn tọa độ'}
              </span>
            </div>
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setIsFullscreen(false);
              }}
              className="p-1.5 rounded-xl bg-slate-100 hover:bg-red-50 hover:text-red-600 text-slate-600 transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
          <div className="flex-1 w-full relative bg-white rounded-b-2xl overflow-hidden shadow-2xl border border-t-0 border-slate-200" onClick={(e) => e.stopPropagation()}>
            {renderMapContent(true)}
          </div>
        </div>
      )}
    </>
  );
}

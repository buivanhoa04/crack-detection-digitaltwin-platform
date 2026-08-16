'use client';

import React, { Suspense, useEffect, useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls, useGLTF, Environment, Html, Bounds } from '@react-three/drei';
import { Loader2 } from 'lucide-react';
import { withAccessToken } from '@/lib/mediaAuth';

interface ModelProps {
  url: string;
}

function GLTFModel({ url }: ModelProps) {
  // useGLTF will auto-fetch and parse the .glb file
  const { scene } = useGLTF(url);
  
  return <primitive object={scene} />;
}

function Loader() {
  return (
    <Html center>
      <div className="flex flex-col items-center justify-center p-4 bg-slate-900/80 backdrop-blur-md rounded-2xl border border-white/10 shadow-2xl">
        <Loader2 className="w-8 h-8 text-blue-500 animate-spin mb-2" />
        <span className="text-white text-xs font-bold uppercase tracking-widest whitespace-nowrap">Đang nạp 3D...</span>
      </div>
    </Html>
  );
}

export default function ModelViewer({ url }: ModelProps) {
  const [proxyUrl, setProxyUrl] = useState('');

  useEffect(() => {
    // If the url is absolute (http), use it directly. 
    // If it's a relative path starting with /files/, wrap it in proxy.
    if (url.startsWith('http')) {
      setProxyUrl(url);
    } else if (url.includes('/files/')) {
      const finalUrl = `${process.env.NEXT_PUBLIC_API_URL || '/api'}/crack/proxy-file?path=${encodeURIComponent(url)}`;
      setProxyUrl(withAccessToken(finalUrl));
    } else {
      setProxyUrl(withAccessToken(url));
    }
  }, [url]);

  if (!proxyUrl) return null;

  return (
    <div className="w-full h-full min-h-[400px] bg-slate-900 rounded-2xl overflow-hidden relative shadow-inner cursor-grab active:cursor-grabbing">
      <Canvas shadows camera={{ position: [0, 5, 10], fov: 45 }}>
        <color attach="background" args={['#0f172a']} /> {/* slate-900 */}
        
        {/* Lights */}
        <ambientLight intensity={0.8} />
        <directionalLight position={[10, 10, 5]} intensity={1.5} castShadow />
        <directionalLight position={[-10, 10, -5]} intensity={0.5} />
        <Environment preset="city" />

        <Suspense fallback={<Loader />}>
          <Bounds fit clip observe margin={1.2}>
            <GLTFModel url={proxyUrl} />
          </Bounds>
        </Suspense>

        <OrbitControls 
          makeDefault 
          autoRotate={false} 
          enablePan={true}
          enableZoom={true}
          enableRotate={true}
          maxDistance={50}
          minDistance={1}
        />
      </Canvas>

      <div className="absolute bottom-4 left-4 bg-black/50 backdrop-blur text-white/70 px-3 py-1.5 rounded-lg text-[9px] font-mono tracking-widest uppercase pointer-events-none border border-white/10">
        Giữ chuột trái để xoay • Cuộn chuột để Zoom
      </div>
    </div>
  );
}

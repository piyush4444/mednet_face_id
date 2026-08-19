import { useState, useEffect, useCallback } from 'react';

export function useWebcams() {
  const [devices, setDevices] = useState([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState('');

  const enumerateDevices = useCallback(async () => {
    try {
      const allDevices = await navigator.mediaDevices.enumerateDevices();
      const videoInputs = allDevices.filter(device => device.kind === 'videoinput');
      
      setDevices(videoInputs);
      
      if (videoInputs.length > 0) {
        // If the selected device doesn't exist anymore, or we haven't selected one
        if (!selectedDeviceId || !videoInputs.find(d => d.deviceId === selectedDeviceId)) {
          setSelectedDeviceId(videoInputs[0].deviceId);
        }
      } else {
        setSelectedDeviceId('');
      }
    } catch (error) {
      console.error("Error enumerating devices:", error);
    }
  }, [selectedDeviceId]);

  useEffect(() => {
    enumerateDevices();
    
    navigator.mediaDevices.addEventListener('devicechange', enumerateDevices);
    return () => {
      navigator.mediaDevices.removeEventListener('devicechange', enumerateDevices);
    };
  }, [enumerateDevices]);

  // Expose an explicit refresh function in case permissions were just granted (labels were blank)
  return { 
    devices, 
    selectedDeviceId, 
    setSelectedDeviceId,
    refreshDevices: enumerateDevices 
  };
}

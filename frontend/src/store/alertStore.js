import { useState } from "react";

export const useAlertStore = () => {
  const [alerts, setAlerts] = useState([]);

  const addAlert = (alert) => {
    setAlerts((prev) => [
      {
        id: Date.now(),
        ...alert,
      },
      ...prev.slice(0, 20),
    ]);
  };

  return { alerts, addAlert };
};

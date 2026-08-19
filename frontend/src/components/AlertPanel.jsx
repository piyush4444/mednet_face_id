export default function AlertPanel({ alerts }) {
  return (
    <div className="w-80 bg-gray-900 text-white h-full p-4 overflow-y-auto">
      <h2 className="text-lg font-semibold mb-4">Alerts</h2>

      {alerts.map((a) => (
        <div
          key={a.id}
          className={`p-3 mb-2 rounded-lg text-sm ${
            a.type === "success"
              ? "bg-green-600"
              : a.type === "warning"
              ? "bg-yellow-600"
              : "bg-red-600"
          }`}
        >
          {a.message}
        </div>
      ))}
    </div>
  );
}

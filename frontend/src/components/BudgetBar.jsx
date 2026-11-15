import { useEffect, useState } from "react";
import { hive, usd } from "../api/client.js";

export default function BudgetBar() {
  const [b, setB] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () => hive.budget().then((x) => alive && setB(x)).catch(() => {});
    load();
    const t = setInterval(load, 15000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  if (!b) return null;
  const pct = (a, l) => Math.min(100, Math.round((a / (l || 1)) * 100));
  return (
    <div className="budget">
      <div className="small muted">Today {usd(b.daily_spent_usd)} / ${b.daily_limit_usd}</div>
      <div className="meter"><span style={{ width: `${pct(b.daily_spent_usd, b.daily_limit_usd)}%` }} /></div>
      <div className="small muted">Month {usd(b.monthly_spent_usd)} / ${b.monthly_limit_usd}</div>
      <div className="meter"><span style={{ width: `${pct(b.monthly_spent_usd, b.monthly_limit_usd)}%` }} /></div>
    </div>
  );
}

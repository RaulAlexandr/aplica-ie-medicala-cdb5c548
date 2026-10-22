import { Odontogram } from './Odontogram';
import { useOdontogramDemo } from './useOdontogramDemo';

export function DemoApp() {
  const state = useOdontogramDemo();
  return <><div className="og-demo-banner">Demonstrație — date locale, fără salvare pe server.</div><Odontogram {...state} onToothSelect={state.setSelectedToothId} /></>;
}

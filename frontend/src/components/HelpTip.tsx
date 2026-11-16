import { useSettings } from '../settings'

export function HelpTip({ text }: { text: string }) {
  const { t } = useSettings()
  return (
    <span className="help-tip">
      <button type="button" className="help-icon" aria-label={t('More information')}>?</button>
      <span className="help-popup" role="tooltip">{text}</span>
    </span>
  )
}

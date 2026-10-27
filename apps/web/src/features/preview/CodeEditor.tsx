import { json } from '@codemirror/lang-json'
import { markdown } from '@codemirror/lang-markdown'
import { python } from '@codemirror/lang-python'
import { EditorView } from '@codemirror/view'
import CodeMirror from '@uiw/react-codemirror'
import { useMemo } from 'react'

/** Editable CodeMirror for JSON, Markdown or Python; the read-only variant is TextPreview. */
export function CodeEditor({
  value,
  onChange,
  language,
  readOnly = false,
  ariaLabel,
}: {
  value: string
  onChange: (value: string) => void
  language: 'json' | 'markdown' | 'python'
  readOnly?: boolean
  ariaLabel?: string
}) {
  const extensions = useMemo(() => {
    const base = [EditorView.lineWrapping]
    if (language === 'json') return [...base, json()]
    if (language === 'markdown') return [...base, markdown()]
    return [...base, python()]
  }, [language])
  return (
    <CodeMirror
      value={value}
      onChange={onChange}
      height="100%"
      aria-label={ariaLabel}
      className="h-full text-[13px] [&_.cm-editor]:h-full [&_.cm-scroller]:overflow-auto"
      extensions={extensions}
      readOnly={readOnly}
      editable={!readOnly}
      basicSetup={{
        lineNumbers: true,
        foldGutter: true,
        highlightActiveLine: !readOnly,
        searchKeymap: true,
      }}
    />
  )
}

import { fireEvent, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { RunCodeArgs, RunCodeBody } from '@/features/chat/toolBodies'
import { renderWithProviders } from '@/test/utils'
import { usePreview } from '@/lib/uiActions'

describe('run_code cards', () => {
  it('shows the purpose and the code on the approval view', () => {
    renderWithProviders(
      <RunCodeArgs
        argsText={JSON.stringify({
          code: "print('hi')\nopen('out/x.txt', 'w').write('x')",
          purpose: 'Count the entries per letter',
          inputs: ['source'],
          timeout_s: 60,
        })}
      />,
    )
    expect(screen.getByText('Count the entries per letter')).toBeInTheDocument()
    expect(screen.getByText(/Inputs: source · time limit 60 s/)).toBeInTheDocument()
    expect(screen.getByText(/print\('hi'\)/)).toBeInTheDocument()
  })

  it('lists outputs that open in the preview, and the logs', () => {
    usePreview.getState().clear()
    renderWithProviders(
      <RunCodeBody
        projectId="p1"
        data={{
          exit_code: 0,
          duration_s: 0.42,
          killed: false,
          stdout: '3 letters\n',
          stderr: '',
          inputs: [{ name: 'oxford.xml', mount_path: 'in/oxford.xml' }],
          outputs: [{ file_id: 'f9', name: 'upper.txt', size: 2048 }],
        }}
      />,
    )
    expect(screen.getByText(/Exit code 0 in 0\.4 s · inputs: in\/oxford\.xml/)).toBeInTheDocument()
    expect(screen.getByText('3 letters')).toBeInTheDocument()
    expect(screen.getByText('2.0 KB')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'upper.txt' }))
    expect(usePreview.getState().target).toEqual({
      kind: 'file',
      fileId: 'f9',
      page: undefined,
      bbox: undefined,
    })
    expect(screen.getByRole('link', { name: 'download' })).toHaveAttribute(
      'href',
      expect.stringContaining('/files/f9/download'),
    )
  })

  it('reports a killed run in red with its stderr', () => {
    renderWithProviders(
      <RunCodeBody
        projectId="p1"
        data={{
          exit_code: 137,
          duration_s: 3.01,
          killed: true,
          stdout: '',
          stderr: '[runner] killed after 3 s (timeout)',
          outputs: [],
        }}
      />,
    )
    const status = screen.getByText(/Killed at the time limit after 3\.0 s/)
    expect(status.className).toContain('text-destructive')
    fireEvent.click(screen.getByRole('button', { name: /Show stderr/ }))
    expect(screen.getByText(/killed after 3 s/)).toBeInTheDocument()
  })
})

import { fireEvent, render, screen } from '@testing-library/react'
import { BrowserRouter, useSearchParams } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'

function SearchNavigationProbe() {
  const [, setSearchParams] = useSearchParams()
  return (
    <button type="button" onClick={() => setSearchParams({ page: 'reagents' })}>
      打开试剂
    </button>
  )
}

describe('console base path', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/console/')
  })

  it('does not duplicate the console prefix when changing query pages', () => {
    render(
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <SearchNavigationProbe />
      </BrowserRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: '打开试剂' }))

    expect(window.location.pathname).toBe('/console/')
    expect(window.location.search).toBe('?page=reagents')
  })
})

'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const navItems = [
  { href: '/', label: 'Overview' },
  { href: '/discover', label: 'Discover' },
  { href: '/gems', label: 'Gems' },
  { href: '/playlists', label: 'Playlists' },
  { href: '/podcasts', label: 'Podcasts' },
];

export default function Navigation() {
  const pathname = usePathname();

  return (
    <nav className="app-navigation" aria-label="Primary navigation">
      <div className="navigation-inner">
        <Link href="/" className="archive-brand" aria-label="Listening Archive home">
          <span className="archive-brand-mark" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span className="archive-brand-copy">
            <strong>Listening Archive</strong>
            <small>Spotify history, decoded</small>
          </span>
        </Link>

        <div className="navigation-links">
          {navItems.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={isActive ? 'page' : undefined}
                className={isActive ? 'navigation-link navigation-link-active' : 'navigation-link'}
              >
                {item.label}
              </Link>
            );
          })}
        </div>
      </div>
    </nav>
  );
}

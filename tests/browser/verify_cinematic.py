# -*- coding: utf-8 -*-
"""Verify public cinematic navigation/search behavior across desktop/mobile and motion preferences."""
from verify import *  # noqa: F401,F403


def cinematic(b):
    reset()
    for mobile in (False, True):
        for theme in ('dark', 'light'):
            for reduced in (False, True):
                label = '%s %s %s' % ('phone' if mobile else 'desktop', theme, 'reduced' if reduced else 'motion')
                ctx, page = open_page(b, 390 if mobile else 1440, 844 if mobile else 900,
                                      mobile=mobile, reduced_motion='reduce' if reduced else 'no-preference',
                                      cookies={'bh_theme': theme, 'bh_wallpaper': 'starcore'})
                check(label + ': motion module loaded', page.evaluate('Boolean(window.HaloMotion)'))
                page.locator('.tab[data-view="settings"]').click()
                page.wait_for_selector('#view-settings.active')
                page.wait_for_timeout(300)
                pill = page.locator('.cine-nav-pill').bounding_box()
                active = page.locator('.tab[data-view="settings"]').bounding_box()
                check(label + ': navigation pill follows current page', pill and active and abs(pill['x']-active['x'])<2 and abs(pill['y']-active['y'])<2)
                page.locator('.tab[data-view="bookmarks"]').click()
                page.wait_for_selector('#view-bookmarks.active');page.wait_for_timeout(300)
                page.locator('#homeSearch').focus()
                check(label + ': search camera engages', page.evaluate('document.documentElement.classList.contains("cine-search")'))
                page.locator('#homeSearch').fill('GitHub')
                page.wait_for_timeout(300)
                check(label + ': search remains usable', page.locator('#homeSearchList').is_visible())
                if reduced:
                    check(label + ': reduced motion has no surrounding transform', page.locator('.home-hero').evaluate('el=>getComputedStyle(el).transform') == 'none')
                if mobile:
                    check(label + ': phone wallpaper has no parallax', page.locator('#homeWall').evaluate('el=>getComputedStyle(el,"::before").transform') == 'none')
                check(label + ': no horizontal overflow', page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                shot(page, 'cine-'+label.replace(' ', '-'))
                ctx.close()
    # API-free browser fallback: direct navigation remains functional without View Transitions.
    ctx, page = open_page(b, 1440, 900)
    page.evaluate('document.startViewTransition = undefined')
    page.locator('.tab[data-view="settings"]').click()
    check('navigation works without View Transitions', page.locator('#view-settings').evaluate('el=>el.classList.contains("active")'))
    ctx.close()


def card_editor(b):
    reset()
    ctx, page = open_page(b, 1440, 900)
    page.evaluate("openLibPage('monitor')")
    page.wait_for_selector('#bmList .bookmark-card')
    page.locator('#bmList .bookmark-card').first.locator('summary.menu-trigger').click()
    page.locator('#bmList .bookmark-card').first.get_by_role('button', name='编辑', exact=True).click()
    page.wait_for_selector('#bmModal.show');page.wait_for_timeout(300)
    check('card expands to an editable dialog', page.locator('#bm_name').input_value() == '中转站 A')
    page.locator('#bmCancel').click();page.wait_for_timeout(250)
    check('cancel returns to the card without saving', not page.locator('#bmModal').is_visible() and page.locator('.cine-card-return').count() == 0)
    check('card data is preserved', '中转站 A' in page.locator('#bmList .bookmark-card').first.inner_text())
    ctx.close()


if __name__ == '__main__':
    main((cinematic, card_editor))

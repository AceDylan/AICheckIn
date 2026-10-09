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


def scifi(b):
    reset()
    ctx, page = open_page(b, 1440, 900, boot=True)
    page.wait_for_timeout(2600)
    check('sci-fi: on by default', page.evaluate("document.documentElement.classList.contains('hub-scifi')"))
    check('sci-fi: opening titles played once and left no overlay',
          'bh_scifi_boot=1' in page.evaluate('document.cookie') and page.locator('.hub-boot').count() == 0)
    check('sci-fi: starfield is drawn', page.locator('.hub-stars').evaluate('el => getComputedStyle(el).display') == 'block'
          and page.locator('.hub-stars').evaluate('el => el.width > 0'))
    page.reload(); page.wait_for_timeout(400)
    check('sci-fi: no second opening within 6 hours', page.locator('.hub-boot').count() == 0)
    page.evaluate("openHomeLook()"); page.wait_for_selector('#scifiSeg')
    page.locator('#scifiSeg [data-scifi="off"]').click()
    check('sci-fi: switch turns it off', not page.evaluate("document.documentElement.classList.contains('hub-scifi')")
          and page.locator('.hub-stars').evaluate('el => getComputedStyle(el).display') == 'none'
          and page.locator('.hub-hud').evaluate('el => getComputedStyle(el).display') == 'none'
          and page.locator('.hub-reactor').evaluate('el => getComputedStyle(el).display') == 'none'
          and 'bh_scifi=off' in page.evaluate('document.cookie'))
    page.locator('#scifiSeg [data-scifi="on"]').click()
    check('sci-fi: and back on', page.evaluate("document.documentElement.classList.contains('hub-scifi')"))
    ctx.close()
    ctx, page = open_page(b, 1440, 900, reduced_motion='reduce', boot=True)
    check('sci-fi: no opening with reduced motion', 'bh_scifi_boot' not in page.evaluate('document.cookie') and page.locator('.hub-boot').count() == 0)
    ctx.close()


def stellar_scene(b):
    reset()
    for mobile in (False, True):
        label = 'phone' if mobile else 'desktop'
        ctx, page = open_page(b, 390 if mobile else 1440, 844 if mobile else 900,
                              mobile=mobile, cookies={'bh_wallpaper': 'starcore'})
        page.wait_for_selector('#homeWall.is-ready')
        image = page.locator('#homeWall').evaluate('el => getComputedStyle(el,"::before").backgroundImage')
        check(label + ': correct starcore composition loaded', ('starcore-m.webp' if mobile else 'starcore.webp') in image)
        check(label + ': wallpaper url carries the asset version', '.webp?v=' in image)
        scene = page.locator('.hub-stars')
        # Observe the rendered surface, without depending on drawing-call structure.
        sample = lambda: scene.evaluate('el => el.toDataURL()')
        before = sample(); page.wait_for_timeout(200)
        check(label + ': orbital scene moves', before != sample())
        page.emulate_media(reduced_motion='reduce'); page.wait_for_timeout(150)
        before = sample(); page.wait_for_timeout(200)
        check(label + ': reduced motion freezes the scene', before == sample())
        page.set_viewport_size({'width': 420 if mobile else 1280, 'height': 800})
        page.wait_for_timeout(200)
        check(label + ': resizing redraws a nonempty still scene', scene.evaluate("el => el.getContext('2d').getImageData(0,0,el.width,el.height).data.some((v,i) => i%4===3 && v>0)"))
        page.evaluate("openHomeLook()"); page.wait_for_selector('#scifiSeg')
        page.locator('#scifiSeg [data-scifi="off"]').click()
        check(label + ': switching effects off preserves the wallpaper', page.locator('#homeWall').is_visible()
              and '/static/wallpapers/starcore' in page.locator('#homeWall').evaluate('el => getComputedStyle(el,"::before").backgroundImage'))
        check(label + ': switching effects off clears the canvas', scene.evaluate("el => !el.getContext('2d').getImageData(0,0,el.width,el.height).data.some((v,i) => i%4===3 && v>0)"))
        page.locator('#scifiSeg [data-scifi="on"]').click()
        page.emulate_media(forced_colors='active'); page.wait_for_timeout(200)
        check(label + ': forced colors hides decoration and retains clock text', scene.evaluate('el => getComputedStyle(el).display') == 'none'
              and page.locator('.hero-clock').evaluate('el => getComputedStyle(el).color') != 'rgba(0, 0, 0, 0)')
        ctx.close()


def scene_budget(b):
    reset()
    ctx = new_context(b, 1440, 900)
    ctx.add_cookies([{'name': 'bh_wallpaper', 'value': 'starcore', 'url': BASE}])
    ctx.add_init_script("Object.defineProperty(navigator, 'connection', { value: { saveData: true } })")
    page = ctx.new_page()
    requests = []
    page.on('request', lambda r: requests.append(r.url))
    page.on('pageerror', lambda e: ERRORS.append(str(e)))
    page.goto(BASE); page.wait_for_selector('.hub-stars'); page.wait_for_timeout(700)
    check('Save-Data: wallpaper is not downloaded', not any('/static/wallpapers/' in u for u in requests))
    scene = page.locator('.hub-stars')
    before = scene.evaluate('el => el.toDataURL()'); page.wait_for_timeout(250)
    check('Save-Data: scene is still without opening titles', before == scene.evaluate('el => el.toDataURL()') and page.locator('.hub-boot').count() == 0)
    ctx.close()
    ctx, page = open_page(b, 1440, 900)
    # Emulate the browser lifecycle signal; inspect pixels rather than private timers.
    page.evaluate("Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(100)
    scene = page.locator('.hub-stars'); before = scene.evaluate('el => el.toDataURL()'); page.wait_for_timeout(250)
    check('background lifecycle: canvas stops drawing', before == scene.evaluate('el => el.toDataURL()'))
    page.evaluate("Object.defineProperty(document, 'hidden', { configurable: true, value: false }); document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(250)
    check('foreground lifecycle: canvas resumes', before != scene.evaluate('el => el.toDataURL()'))
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
    page.locator('#bmCancel').click()
    # the return ghost lives ~180ms (and is removed after 400ms at the latest); allow slow software rendering
    page.wait_for_function("() => document.querySelectorAll('.cine-card-return').length === 0", timeout=1500)   # 函数形式：页面 CSP 不许字符串求值
    check('cancel returns to the card without saving', not page.locator('#bmModal').is_visible() and page.locator('.cine-card-return').count() == 0)
    check('card data is preserved', '中转站 A' in page.locator('#bmList .bookmark-card').first.inner_text())
    ctx.close()


if __name__ == '__main__':
    main((cinematic, card_editor, scifi, stellar_scene, scene_budget), os.environ.get('BH_VERIFY_RESULTS', ''))

document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.querySelector('.menu-toggle');
  const menu = document.querySelector('.nav-menu');
  if (toggle && menu) {
    toggle.addEventListener('click', () => {
      const expanded = toggle.getAttribute('aria-expanded') === 'true';
      toggle.setAttribute('aria-expanded', String(!expanded));
      menu.classList.toggle('is-open', !expanded);
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') {
        toggle.setAttribute('aria-expanded', 'false');
        menu.classList.remove('is-open');
        toggle.focus();
      }
    });
  }
  document.querySelectorAll('img[data-featured-generic]').forEach((image) => {
    const useGenericBanner = () => image.remove();
    image.addEventListener('error', useGenericBanner, { once: true });
    if (image.complete && image.naturalWidth === 0) useGenericBanner();
  });
  document.querySelectorAll('img[data-banner-fallback]').forEach((image) => {
    const useLocalBanner = () => {
      if (!image.dataset.bannerFallback) return;
      image.src = image.dataset.bannerFallback;
      image.alt = '';
      delete image.dataset.bannerFallback;
    };
    image.addEventListener('error', useLocalBanner, { once: true });
    if (image.complete && image.naturalWidth === 0) useLocalBanner();
  });
  document.querySelectorAll('img[data-fallback], img[data-category]').forEach((image) => {
    const showFallback = () => {
      if (!image.isConnected) return;
      const replacement = document.createElement('span');
      replacement.setAttribute('aria-hidden', 'true');
      if (image.hasAttribute('data-category')) {
        const artwork = image.closest('.event-art');
        if (artwork) artwork.classList.add('category-art');
        replacement.textContent = image.dataset.category || 'Competition';
      } else {
        replacement.textContent = image.dataset.fallback || 'E';
        if (image.closest('.team-name')) replacement.className = 'team-initial';
      }
      image.replaceWith(replacement);
    };
    image.addEventListener('error', showFallback, { once: true });
    if (image.complete && image.naturalWidth === 0) showFallback();
  });
  const featuredForm = document.querySelector('.featured-controls');
  if (featuredForm) {
    const searchSubmitter = featuredForm.querySelector('.featured-apply');
    featuredForm.querySelector('input[type="search"]').addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        featuredForm.requestSubmit(searchSubmitter);
      }
    });
    featuredForm.querySelectorAll('select').forEach(select => select.addEventListener('change', () => {
      featuredForm.requestSubmit(searchSubmitter);
    }));
  }
  document.querySelectorAll('.filter-panel, .featured-controls').forEach((form) => {
    let searchTimer;
    const submitFilters = () => {
      clearTimeout(searchTimer);
      if (form.getAttribute('aria-busy') === 'true') return;
      const submitter = form.querySelector('.featured-apply');
      if (submitter) form.requestSubmit(submitter);
      else form.requestSubmit();
    };
    form.querySelectorAll('select, input[type="date"], input[type="radio"]').forEach((control) => {
      if (form.classList.contains('featured-controls') && control.tagName === 'SELECT') return;
      control.addEventListener('change', () => {
        const selectedDay = form.querySelector('input[name="day"]');
        if (selectedDay && control.name === 'date') selectedDay.value = control.value || 'all';
        submitFilters();
      });
    });
    form.querySelectorAll('input[type="search"]').forEach((control) => {
      control.addEventListener('input', (event) => {
        clearTimeout(searchTimer);
        if (!event.isComposing) searchTimer = setTimeout(submitFilters, 500);
      });
      control.addEventListener('compositionend', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(submitFilters, 500);
      });
    });
    form.addEventListener('submit', () => {
      clearTimeout(searchTimer);
      const loading = form.querySelector('.filter-loading, .featured-loading');
      if (loading) loading.hidden = false;
      form.setAttribute('aria-busy', 'true');
    });
  });
  const dayNavigation = document.querySelector('.schedule-day-nav');
  const selectedDayTab = dayNavigation?.querySelector('[aria-current]');
  if (selectedDayTab) {
    dayNavigation.scrollLeft += selectedDayTab.getBoundingClientRect().left
      - dayNavigation.getBoundingClientRect().left
      - (dayNavigation.clientWidth - selectedDayTab.offsetWidth) / 2;
  }
  const standingsFilters = document.getElementById('standings-filters');
  if (standingsFilters) {
    const search = document.getElementById('standings-search');
    const category = document.getElementById('standings-category');
    const event = document.getElementById('standings-event');
    const year = document.getElementById('standings-year');
    let searchTimer;
    const submitStandings = () => {
      clearTimeout(searchTimer);
      standingsFilters.requestSubmit();
    };
    standingsFilters.addEventListener('submit', () => clearTimeout(searchTimer));
    search.addEventListener('input', (inputEvent) => {
      clearTimeout(searchTimer);
      if (!inputEvent.isComposing) searchTimer = setTimeout(submitStandings, 500);
    });
    search.addEventListener('compositionstart', () => clearTimeout(searchTimer));
    search.addEventListener('compositionend', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(submitStandings, 500);
    });
    category.addEventListener('change', () => {
      event.value = '';
      submitStandings();
    });
    event.addEventListener('change', submitStandings);
    year.addEventListener('change', () => {
      category.value = '';
      event.value = '';
      submitStandings();
    });
  }
  const officialBracket = document.querySelector('[data-official-bracket]');
  if (officialBracket) {
    const drawBracket = () => {
      const svg = officialBracket.querySelector('.official-bracket-connectors');
      const bounds = officialBracket.getBoundingClientRect();
      const cards = new Map(Array.from(officialBracket.querySelectorAll('[data-match-id]'),
        (card) => [card.dataset.matchId, card]));
      svg.replaceChildren();
      const connect = (source, target, loser = false) => {
        const start = source.getBoundingClientRect();
        const end = target.getBoundingClientRect();
        const x1 = start.right - bounds.left;
        const y1 = start.top + start.height / 2 - bounds.top;
        const x2 = end.left - bounds.left;
        const y2 = end.top + end.height / 2 - bounds.top;
        const bend = x2 > x1 ? (x1 + x2) / 2 : x1 + 12;
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', `M ${x1} ${y1} H ${bend} V ${y2} H ${x2}`);
        if (loser) path.classList.add('loser-connector');
        svg.append(path);
      };
      cards.forEach((card) => {
        const winnerTarget = cards.get(card.dataset.nextWinner);
        const loserTarget = cards.get(card.dataset.nextLoser);
        if (winnerTarget && winnerTarget !== card) connect(card, winnerTarget);
        if (loserTarget && loserTarget !== card) connect(card, loserTarget, true);
      });
    };
    new ResizeObserver(drawBracket).observe(officialBracket);
    if (document.fonts) document.fonts.ready.then(drawBracket);
    drawBracket();
  }
  window.addEventListener('pageshow', () => {
    document.querySelectorAll('.filter-panel, .featured-controls').forEach((form) => {
      form.removeAttribute('aria-busy');
      const loading = form.querySelector('.filter-loading, .featured-loading');
      if (loading) loading.hidden = true;
    });
  });
});

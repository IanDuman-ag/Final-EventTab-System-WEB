"""Curated title/category matching, shared by creation, worker and public pages."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CoverDefinition:
    key: str
    subject: str
    keywords: tuple
    bundled: str = ''


COVERS = (
    CoverDefinition('table_tennis', 'table tennis paddles, ball and competition table', ('table tennis', 'ping pong')),
    CoverDefinition('pickleball', 'pickleball paddles and ball on a real competition court', ('pickleball', 'pickle ball')),
    CoverDefinition('pageant', 'an elegant pageant crown on a dark velvet stage', ('pageant', 'ambassador', 'mr. & ms.', 'mr and ms'), 'pageant.webp'),
    CoverDefinition('dance', 'university hip-hop dancers performing on a dramatic stage', ('dance', 'dancing', 'hip-hop', 'hip hop', 'hiphop', 'cheerdance'), 'dance.webp'),
    CoverDefinition('duet', 'two microphones on a university vocal duet performance stage', ('duet',), 'duet.webp'),
    CoverDefinition('band', 'musicians performing with guitars, drums and microphones on stage', ('band', 'showchoir', 'yell')),
    CoverDefinition('vocal', 'a professional microphone on a vocal performance stage', ('vocal', 'singing', 'karaoke'), 'singing.webp'),
    CoverDefinition('photography', 'a professional camera and printed photographs at a student photo contest', ('photo',), 'photography-cover.webp'),
    CoverDefinition('poster', 'student hands making an artistic poster with paints and brushes, no words', ('poster', 'banner', 'designing'), 'art-cover.webp'),
    CoverDefinition('painting', 'student hands drawing with charcoal and paint at an art competition', ('painting', 'charcoal', 'drawing'), 'art-cover.webp'),
    CoverDefinition('quiz', 'a university quiz bee stage with contestant podiums and yellow buzzers', ('quiz', 'spelling bee'), 'quiz-cover.webp'),
    CoverDefinition('radio', 'a radio drama recording studio with microphones and headphones', ('radio',)),
    CoverDefinition('speech', 'a university speech competition with a microphone and elegant podium', ('speech', 'debate', 'poetry', 'talumpati')),
    CoverDefinition('theater', 'a university theater performance with actors on a cinematic stage', ('play', 'drama', 'theater', 'theatre', 'film')),
    CoverDefinition('badminton', 'a shuttlecock and badminton racket beside the competition net', ('badminton',), 'badminton.webp'),
    CoverDefinition('basketball', 'university athletes playing basketball in an indoor arena', ('basketball',), 'basketball.webp'),
    CoverDefinition('volleyball', 'university volleyball athletes playing beside the net', ('volleyball',)),
    CoverDefinition('chess', 'a professional chess tournament board with dramatic lighting', ('chess',)),
    CoverDefinition('darts', 'competition darts in a real dartboard', ('dart',)),
    CoverDefinition('sepak_takraw', 'a woven sepak takraw ball beside a court net', ('sepak', 'takraw')),
    CoverDefinition('football', 'a football and university sports field', ('football', 'soccer', 'futsal')),
    CoverDefinition('race', 'university athletes at an intramural outdoor relay activity', ('race', 'relay', 'fiesta')),
    CoverDefinition('discipline', 'a ceremonial trophy at a university team recognition event', ('disciplined', 'character', 'unit')),
    CoverDefinition('intramurals', 'a gold trophy in a university sports arena', (), 'intramurals-cover.webp'),
)
BY_KEY = {cover.key: cover for cover in COVERS}


def cover_for_event(event):
    # Specific title/sport matches outrank broad categories like "Sports".
    title = ' '.join(str(getattr(event, field, '') or '') for field in (
        'name', 'sport_type', 'special_event_type')).casefold()
    for text in (title, str(event.category or '').casefold()):
        for cover in COVERS:
            if any(keyword in text for keyword in cover.keywords):
                return cover
    return BY_KEY['intramurals']

"""Regression checks for MarkdownV2 escaping in the public channel formatter."""
from message_formatter import JobMessageFormatter

fmt = JobMessageFormatter()


def test_escape_markdown_v2():
    assert fmt._escape_markdown_v2('a | b') == 'a \\| b', f"FAIL pipe: {fmt._escape_markdown_v2('a | b')!r}"
    result = fmt._escape_markdown_v2('a \\| b')
    assert result == 'a \\\\\\| b', f"FAIL escaped pipe: {result!r}"
    assert fmt._escape_markdown_v2('_*[]()~`>#+-=|{}.!') == \
        '\\_\\*\\[\\]\\(\\)\\~\\`\\>\\#\\+\\-\\=\\|\\{\\}\\.\\!'
    print("OK: _escape_markdown_v2")


def test_escape_url():
    assert fmt._escape_url('https://example.com/job|123') == \
        'https://example.com/job%7C123', "FAIL URL pipe"
    assert fmt._escape_url('https://example.com/job)123') == \
        'https://example.com/job%29123', "FAIL URL paren"
    assert fmt._escape_url('https://example.com/job\\123') == \
        'https://example.com/job%5C123', "FAIL URL backslash"
    print("OK: _escape_url")


def _job(level='Junior'):
    return {
        'title': 'Python Developer | Django',
        'company': 'Company | Subsidiary',
        'level': level,
        'category': 'development',
        'primary_track': 'development',
        'salary': '$3000 | $5000',
        'location': 'Remote | Worldwide',
        'url': 'https://example.com/job|123',
        'description': 'Use Python | Django | React to build production APIs and services.',
        'tags': ['Python', 'Django'],
        'source': 'Test | Source',
        'hash': 'abc',
    }


def test_format_compact():
    text = fmt._format_compact(_job())
    assert 'Python Developer \\| Django' in text
    assert 'Company \\| Subsidiary' in text
    assert 'Remote \\| Worldwide' in text
    assert '$3000 \\| $5000' in text
    assert 'Use Python \\| Django \\| React' in text
    assert 'https://example.com' not in text
    lines = text.split('\n')
    for line in lines:
        if ' | ' in line and ' \\| ' not in line:
            raise AssertionError(f"Неэкранированный pipe в строке: {line!r}")
    print("OK: _format_compact")


def test_format_full():
    text = fmt._format_full(_job('Middle'))
    assert 'Python Developer \\| Django' in text
    assert 'Company \\| Subsidiary' in text
    assert 'Remote \\| Worldwide' in text
    assert '$3000 \\| $5000' in text
    assert 'Test \\| Source' in text
    assert 'https://example.com' not in text
    print("OK: _format_full")


def test_format_job_list():
    jobs = [{
        'title': 'Dev | Ops',
        'company': 'Corp | Inc',
        'level': 'Junior',
        'category': 'devops',
    }]
    text = fmt.format_job_list(jobs)
    assert 'Dev \\| Ops' in text
    assert 'Corp \\| Inc' in text
    print("OK: format_job_list")


if __name__ == '__main__':
    test_escape_markdown_v2()
    test_escape_url()
    test_format_compact()
    test_format_full()
    test_format_job_list()
    print("\nВСЕ ТЕСТЫ ПРОЙДЕНЫ!")

"""
Test script для проверки фикса MarkdownV2 экранирования символа '|'
"""
import re
from message_formatter import JobMessageFormatter

fmt = JobMessageFormatter()


def test_escape_markdown_v2():
    """Тест _escape_markdown_v2"""
    assert fmt._escape_markdown_v2('a | b') == 'a \\| b', f"FAIL pipe: {fmt._escape_markdown_v2('a | b')!r}"
    
    result = fmt._escape_markdown_v2('a \\| b')
    assert result == 'a \\\\\\| b', f"FAIL escaped pipe: {result!r}"
    
    assert fmt._escape_markdown_v2('_*[]()~`>#+-=|{}.!') == \
        '\\_\\*\\[\\]\\(\\)\\~\\`\\>\\#\\+\\-\\=\\|\\{\\}\\.\\!'
    
    print("OK: _escape_markdown_v2")


def test_escape_url():
    """Тест _escape_url"""
    assert fmt._escape_url('https://example.com/job|123') == \
        'https://example.com/job%7C123', "FAIL URL pipe"
    assert fmt._escape_url('https://example.com/job)123') == \
        'https://example.com/job%29123', "FAIL URL paren"
    assert fmt._escape_url('https://example.com/job\\123') == \
        'https://example.com/job%5C123', "FAIL URL backslash"
    print("OK: _escape_url")


def test_format_compact():
    """Тест _format_compact с | в полях"""
    job = {
        'title': 'Python Developer | Django',
        'company': 'Company | Subsidiary',
        'level': 'Junior',
        'category': 'development',
        'salary': '$3000 | $5000',
        'location': 'Remote | Worldwide',
        'url': 'https://example.com/job|123',
        'description': 'Use Python | Django | React',
        'tags': ['Python', 'Django'],
        'source': 'Test',
        'hash': 'abc',
    }
    text = fmt._format_compact(job)
    
    assert 'Python Developer \\| Django' in text
    assert 'Company \\| Subsidiary' in text
    assert 'Remote \\| Worldwide' in text
    assert '$3000 \\| $5000' in text
    assert 'job%7C123' in text
    
    lines = text.split('\n')
    for line in lines:
        if ' | ' in line and ' \\| ' not in line:
            raise AssertionError(f"Неэкранированный pipe в строке: {line!r}")
    
    print("OK: _format_compact")


def test_format_full():
    """Тест _format_full с | в полях"""
    job = {
        'title': 'Python Developer | Django',
        'company': 'Company | Subsidiary',
        'level': 'Middle',
        'category': 'development',
        'salary': '$3000 | $5000',
        'location': 'Remote | Worldwide',
        'description': 'Use Python | Django | React',
        'tags': ['Python', 'Django'],
        'source': 'Test | Source',
        'url': 'https://example.com/job|123',
        'hash': 'abc',
    }
    text = fmt._format_full(job)
    
    assert 'Python Developer \\| Django' in text
    assert 'Company \\| Subsidiary' in text
    assert 'Remote \\| Worldwide' in text
    assert '$3000 \\| $5000' in text
    assert 'Test \\| Source' in text
    assert 'job%7C123' in text
    
    print("OK: _format_full")


def test_format_job_list():
    """Тест format_job_list с | в полях"""
    jobs = [
        {
            'title': 'Dev | Ops',
            'company': 'Corp | Inc',
            'level': 'Junior',
            'category': 'devops',
        }
    ]
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

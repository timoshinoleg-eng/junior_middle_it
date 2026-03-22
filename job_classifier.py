"""
Job Classifier Module - Автоматическая классификация вакансий по категориям
Версия: 1.0.0
"""
import re
from typing import Dict, Optional, List, Set
from dataclasses import dataclass


@dataclass
class CategoryWeights:
    """Веса для определения категории"""
    name: str
    emoji: str
    keywords: Dict[str, int]  # keyword -> weight
    title_bonus: Dict[str, int]  # дополнительные веса для заголовка


class JobClassifier:
    """
    Классификатор вакансий на основе keyword-matching с весовой системой.
    Поддерживает 7 категорий: dev, qa, devops, data, marketing, sales, pm
    """
    
    # Эмодзи для категорий
    CATEGORY_EMOJIS = {
        'development': '💻',
        'qa': '🧪',
        'devops': '🔧',
        'data': '📊',
        'marketing': '📢',
        'sales': '💼',
        'pm': '📋',
        'design': '🎨',
        'support': '🎧',
        'security': '🔒',
        'other': '📌'
    }
    
    # Названия категорий на русском
    CATEGORY_NAMES = {
        'development': 'Разработка',
        'qa': 'QA / Тестирование',
        'devops': 'DevOps / Инфраструктура',
        'data': 'Данные / Аналитика',
        'marketing': 'Маркетинг',
        'sales': 'Продажи',
        'pm': 'Управление проектами',
        'design': 'Дизайн / UX-UI',
        'support': 'Поддержка',
        'security': 'Безопасность',
        'other': 'Другое'
    }
    
    def __init__(self):
        self.categories = self._init_categories()
        self.conflict_resolvers = self._init_conflict_resolvers()
    
    def _init_categories(self) -> Dict[str, CategoryWeights]:
        """Инициализация категорий с весами ключевых слов"""
        return {
            'development': CategoryWeights(
                name='development',
                emoji='💻',
                keywords={
                    # Языки программирования
                    'python': 3, 'javascript': 3, 'typescript': 3, 'java': 3, 'c++': 3, 'c#': 3,
                    'go': 3, 'golang': 3, 'rust': 3, 'php': 3, 'ruby': 3, 'swift': 3, 'kotlin': 3,
                    'scala': 3, 'perl': 2, 'r': 2, 'matlab': 2,
                    # Фреймворки и технологии
                    'react': 3, 'vue': 3, 'angular': 3, 'svelte': 3, 'next.js': 3, 'nuxt': 3,
                    'django': 3, 'flask': 3, 'fastapi': 3, 'spring': 3, 'laravel': 3, 'rails': 3,
                    'express': 3, 'node.js': 3, 'nodejs': 3, '.net': 3, 'asp.net': 3,
                    # Роли
                    'developer': 4, 'разработчик': 4, 'программист': 4, 'engineer': 3, 'инженер': 3,
                    'frontend': 4, 'backend': 4, 'full-stack': 4, 'fullstack': 4, 'web': 2,
                    'mobile': 3, 'ios': 3, 'android': 3, 'flutter': 3, 'react native': 3,
                    'software': 2, 'приложений': 2, 'architect': 2, 'архитектор': 2,
                    # Базы данных
                    'sql': 2, 'postgresql': 2, 'mysql': 2, 'mongodb': 2, 'redis': 2,
                },
                title_bonus={
                    'developer': 5, 'разработчик': 5, 'engineer': 4, 'программист': 5,
                    'frontend': 4, 'backend': 4, 'full-stack': 4, 'fullstack': 4,
                    'python': 3, 'javascript': 3, 'java': 3, 'react': 3, 'vue': 3,
                }
            ),
            
            'qa': CategoryWeights(
                name='qa',
                emoji='🧪',
                keywords={
                    'qa': 5, 'test': 4, 'testing': 4, 'тестировщик': 5, 'тестирование': 5,
                    'automation': 3, 'автоматизация': 3, 'manual': 3, 'ручное': 3,
                    'selenium': 3, 'cypress': 3, 'playwright': 3, 'junit': 2, 'pytest': 2,
                    'tester': 4, 'qa engineer': 5, 'sdet': 5, 'quality assurance': 5,
                    'bug': 2, 'defect': 2, 'регрессия': 2, 'regression': 2,
                    'test case': 3, 'test plan': 3, 'qa automation': 5, 'тест-автоматизация': 4,
                },
                title_bonus={
                    'qa': 6, 'test': 5, 'testing': 5, 'тестировщик': 6, 'тестирование': 6,
                    'qa engineer': 6, 'sdet': 6, 'automation qa': 6,
                }
            ),
            
            'devops': CategoryWeights(
                name='devops',
                emoji='🔧',
                keywords={
                    'devops': 5, 'sre': 5, 'site reliability': 5, 'platform engineer': 4,
                    'docker': 3, 'kubernetes': 4, 'k8s': 4, 'terraform': 3, 'ansible': 3,
                    'jenkins': 3, 'gitlab ci': 3, 'github actions': 3, 'ci/cd': 4, 'cicd': 4,
                    'aws': 3, 'azure': 3, 'gcp': 3, 'cloud': 2, 'облако': 2, 'облачные': 2,
                    'infrastructure': 3, 'инфраструктура': 3, 'deployment': 2, 'развертывание': 2,
                    'linux': 2, 'ubuntu': 1, 'centos': 1, 'debian': 1, 'server': 2, 'сервер': 2,
                    'monitoring': 2, 'мониторинг': 2, 'prometheus': 2, 'grafana': 2, 'datadog': 2,
                    'nginx': 2, 'apache': 1, 'load balancer': 2, 'балансировщик': 2,
                    'automation': 2, 'автоматизация': 2, 'scripting': 2, 'bash': 2, 'powershell': 2,
                },
                title_bonus={
                    'devops': 6, 'sre': 6, 'site reliability': 5, 'platform': 4,
                    'kubernetes': 4, 'docker': 3, 'cloud': 3, 'aws': 3,
                }
            ),
            
            'data': CategoryWeights(
                name='data',
                emoji='📊',
                keywords={
                    'data': 3, 'данные': 3, 'аналитик': 4, 'analyst': 4, 'analytics': 3,
                    'data scientist': 5, 'data engineer': 5, 'data science': 5, 'ml engineer': 5,
                    'machine learning': 4, 'deep learning': 4, 'nlp': 3, 'computer vision': 3,
                    'pandas': 2, 'numpy': 2, 'scikit-learn': 2, 'tensorflow': 2, 'pytorch': 2,
                    'sql': 2, 'bigquery': 2, 'spark': 2, 'hadoop': 2, 'kafka': 2,
                    'tableau': 2, 'power bi': 2, 'looker': 2, 'superset': 1,
                    'etl': 3, 'data pipeline': 3, 'data warehouse': 3, 'bi': 2,
                    'статистика': 2, 'statistics': 2, 'mlops': 4, 'a/b тест': 2, 'ab test': 2,
                },
                title_bonus={
                    'data': 5, 'аналитик': 5, 'analyst': 5, 'data scientist': 6,
                    'data engineer': 6, 'machine learning': 5, 'ml': 4, 'ai': 4,
                }
            ),
            
            'marketing': CategoryWeights(
                name='marketing',
                emoji='📢',
                keywords={
                    'marketing': 5, 'маркетинг': 5, 'маркетолог': 5, 'digital marketing': 5,
                    'seo': 4, 'sem': 4, 'ppc': 4, 'ads': 3, 'advertising': 3, 'реклама': 3,
                    'content': 3, 'контент': 3, 'copywriter': 4, 'копирайтер': 4, 'smm': 4,
                    'social media': 3, 'email marketing': 4, 'growth': 3, 'lead generation': 3,
                    'аналитика маркетинга': 4, 'marketing analyst': 4, 'brand': 3, 'бренд': 3,
                    'product marketing': 5, 'cmo': 4, 'marketing manager': 5,
                    'google analytics': 2, 'yandex metrica': 2, 'facebook ads': 2, 'target': 2,
                },
                title_bonus={
                    'marketing': 6, 'маркетинг': 6, 'маркетолог': 6, 'cmo': 5,
                    'digital marketing': 5, 'product marketing': 5,
                }
            ),
            
            'sales': CategoryWeights(
                name='sales',
                emoji='💼',
                keywords={
                    'sales': 5, 'продажи': 5, 'продавец': 4, 'sales manager': 5, 'account executive': 5,
                    'ae': 3, 'sdr': 4, 'bdr': 4, 'business development': 4, 'lead generation': 3,
                    'customer success': 4, 'csm': 3, 'account manager': 4, 'key account': 4,
                    'presales': 4, 'solutions engineer': 3, 'sales engineer': 4,
                    'торговый представитель': 4, 'менеджер по продажам': 5, 'менеджер по работе с клиентами': 4,
                    'cold calling': 3, 'crm': 2, 'salesforce': 2, 'hubspot': 2, 'pipeline': 2,
                    'quota': 2, 'revenue': 1, 'commission': 2, 'комиссия': 2,
                },
                title_bonus={
                    'sales': 6, 'продажи': 6, 'sales manager': 6, 'account executive': 5,
                    'sdr': 5, 'bdr': 5, 'account manager': 5,
                }
            ),
            
            'pm': CategoryWeights(
                name='pm',
                emoji='📋',
                keywords={
                    'product manager': 5, 'project manager': 5, 'program manager': 5,
                    'продакт': 5, 'проджект': 5, 'менеджер проекта': 5, 'менеджер продукта': 5,
                    'pm': 4, 'po': 4, 'product owner': 5, 'scrum master': 5, 'scrum': 3,
                    'agile': 3, 'kanban': 2, 'jira': 2, 'confluence': 1, 'trello': 1,
                    'roadmap': 2, 'backlog': 2, 'sprint': 2, 'user story': 2, 'epic': 1,
                    'stakeholder': 2, 'stakeholders': 2, 'команда': 1, 'team': 1,
                    'waterfall': 2, 'pmo': 4, 'delivery manager': 4, 'release manager': 4,
                    'technical program manager': 5, 'tpm': 4, 'ит-проект': 3, 'digital product': 3,
                },
                title_bonus={
                    'product manager': 6, 'project manager': 6, 'pm': 5, 'po': 5,
                    'product owner': 6, 'scrum master': 6, 'продакт': 6, 'проджект': 6,
                }
            ),
            
            'design': CategoryWeights(
                name='design',
                emoji='🎨',
                keywords={
                    'designer': 5, 'дизайнер': 5, 'ux': 4, 'ui': 4, 'ux/ui': 5, 'ui/ux': 5,
                    'product design': 4, 'graphic design': 4, 'web design': 4, 'visual design': 4,
                    'motion design': 4, '3d': 2, 'illustrator': 3, 'illustration': 3, 'фигма': 3,
                    'figma': 3, 'sketch': 3, 'adobe': 2, 'photoshop': 2, 'after effects': 2,
                    'prototyping': 3, 'wireframing': 3, 'user research': 3, 'usability': 2,
                    'design system': 3, 'brand design': 3, 'creative': 2, 'креатив': 2,
                },
                title_bonus={
                    'designer': 6, 'дизайнер': 6, 'ux': 5, 'ui': 5, 'ux/ui': 6, 'ui/ux': 6,
                    'product design': 5, 'design lead': 5,
                }
            ),
            
            'security': CategoryWeights(
                name='security',
                emoji='🔒',
                keywords={
                    'security': 4, 'cybersecurity': 5, 'information security': 5, 'infosec': 5,
                    'безопасность': 4, 'кибербезопасность': 5, 'pentest': 4, 'penetration': 4,
                    'ethical hacker': 5, 'security engineer': 5, 'security analyst': 5,
                    'soc': 3, 'siem': 3, 'vulnerability': 2, 'уязвимость': 2, 'audit': 2, 'аудит': 2,
                    'compliance': 2, 'gdpr': 2, 'iso 27001': 2, 'firewall': 1, 'antivirus': 1,
                },
                title_bonus={
                    'security': 5, 'cybersecurity': 6, 'infosec': 6, 'pentest': 5,
                    'security engineer': 6, 'security analyst': 6,
                }
            ),
            
            'support': CategoryWeights(
                name='support',
                emoji='🎧',
                keywords={
                    'support': 4, 'поддержка': 4, 'helpdesk': 4, 'service desk': 4,
                    'technical support': 5, 'it support': 5, 'customer support': 4,
                    'help desk': 4, 'service desk': 4, 'техподдержка': 5, 'клиентская поддержка': 4,
                    'troubleshooting': 2, 'ticket': 1, 'incident': 1, 'sla': 2,
                },
                title_bonus={
                    'support': 5, 'technical support': 6, 'it support': 6, 'helpdesk': 5,
                }
            ),
        }
    
    def _init_conflict_resolvers(self) -> List[tuple]:
        """
        Правила разрешения конфликтов между категориями.
        Формат: (категория_источник, категория_цель, условия)
        Если условия выполнены, приоритет отдается категории_цель
        """
        return [
            # Data Engineer -> data (не development)
            ('development', 'data', lambda job: 'data engineer' in job.get('title', '').lower()),
            # QA Automation -> qa (не development)
            ('development', 'qa', lambda job: any(kw in job.get('title', '').lower() for kw in ['qa automation', 'test automation', 'automation qa'])),
            # DevOps с упором на development -> devops
            ('development', 'devops', lambda job: any(kw in job.get('title', '').lower() for kw in ['platform engineer', 'sre', 'site reliability'])),
            # Security Engineer -> security (не development)
            ('development', 'security', lambda job: 'security engineer' in job.get('title', '').lower()),
            # Data Analyst -> data (не development)
            ('development', 'data', lambda job: 'data analyst' in job.get('title', '').lower() or 'аналитик данных' in job.get('title', '').lower()),
            # Marketing Analyst -> marketing (не data)
            ('data', 'marketing', lambda job: 'marketing analyst' in job.get('title', '').lower() or 'маркетинговый аналитик' in job.get('title', '').lower()),
            # Sales Engineer -> sales (не development)
            ('development', 'sales', lambda job: 'sales engineer' in job.get('title', '').lower() or 'presales' in job.get('title', '').lower()),
            # Product Marketing -> marketing (не pm)
            ('pm', 'marketing', lambda job: 'product marketing' in job.get('title', '').lower()),
        ]
    
    def _normalize_text(self, text: str) -> str:
        """Нормализация текста для анализа"""
        if not text:
            return ''
        # Приводим к нижнему регистру, заменяем спецсимволы
        text = text.lower()
        text = re.sub(r'[^\w\s/+]', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    
    def _calculate_category_score(self, job: Dict, category: CategoryWeights) -> int:
        """Подсчет веса категории для вакансии"""
        title = self._normalize_text(job.get('title', ''))
        description = self._normalize_text(job.get('description', ''))
        
        score = 0
        
        # Веса для заголовка (с бонусом)
        for keyword, weight in category.keywords.items():
            normalized_kw = keyword.lower()
            # Проверяем заголовок с бонусом
            title_bonus = category.title_bonus.get(keyword, 0)
            if normalized_kw in title:
                score += weight + title_bonus
            elif normalized_kw in description:
                score += weight
        
        return score
    
    def classify(self, job: Dict) -> str:
        """
        Классификация вакансии по категориям.
        Возвращает код категории (development, qa, devops, и т.д.)
        """
        scores = {}
        
        # Считаем веса для всех категорий
        for cat_key, category in self.categories.items():
            scores[cat_key] = self._calculate_category_score(job, category)
        
        # Находим категорию с максимальным весом
        if not scores or max(scores.values()) == 0:
            return 'other'
        
        best_category = max(scores, key=scores.get)
        best_score = scores[best_category]
        
        # Проверяем конфликты и применяем правила разрешения
        for source_cat, target_cat, condition in self.conflict_resolvers:
            if best_category == source_cat and condition(job):
                # Проверяем, что целевая категория тоже имеет ненулевой вес
                if scores.get(target_cat, 0) > 0:
                    best_category = target_cat
                    break
        
        # Минимальный порог уверенности
        if best_score < 2:
            return 'other'
        
        return best_category
    
    def get_category_info(self, category_code: str) -> Dict:
        """Получение информации о категории"""
        category = self.categories.get(category_code)
        if category:
            return {
                'code': category_code,
                'name': self.CATEGORY_NAMES.get(category_code, category_code),
                'emoji': category.emoji,
            }
        return {
            'code': category_code,
            'name': self.CATEGORY_NAMES.get(category_code, category_code),
            'emoji': self.CATEGORY_EMOJIS.get(category_code, '📌'),
        }
    
    def get_all_categories(self) -> List[Dict]:
        """Получение списка всех категорий"""
        return [
            self.get_category_info(code)
            for code in self.categories.keys()
        ]
    
    def batch_classify(self, jobs: List[Dict]) -> List[Dict]:
        """Классификация списка вакансий"""
        for job in jobs:
            job['category'] = self.classify(job)
            cat_info = self.get_category_info(job['category'])
            job['category_name'] = cat_info['name']
            job['category_emoji'] = cat_info['emoji']
        return jobs


# Singleton instance
_classifier = None


def get_classifier() -> JobClassifier:
    """Получение singleton-экземпляра классификатора"""
    global _classifier
    if _classifier is None:
        _classifier = JobClassifier()
    return _classifier


def classify_job(job: Dict) -> str:
    """Удобная функция для классификации одной вакансии"""
    return get_classifier().classify(job)


def get_job_category_info(job: Dict) -> Dict:
    """Получение полной информации о категории вакансии"""
    classifier = get_classifier()
    category = classifier.classify(job)
    return classifier.get_category_info(category)


if __name__ == '__main__':
    # Тестирование классификатора
    test_jobs = [
        {'title': 'Senior Python Developer', 'description': 'We need a Python developer'},
        {'title': 'QA Automation Engineer', 'description': 'Selenium, pytest'},
        {'title': 'DevOps Engineer', 'description': 'Kubernetes, Docker, AWS'},
        {'title': 'Data Scientist', 'description': 'Machine learning, Python, SQL'},
        {'title': 'Product Manager', 'description': 'Agile, Scrum, Jira'},
        {'title': 'UX/UI Designer', 'description': 'Figma, prototyping'},
        {'title': 'Sales Manager', 'description': 'B2B sales, CRM'},
        {'title': 'Marketing Specialist', 'description': 'SEO, SMM, content'},
    ]
    
    classifier = JobClassifier()
    for job in test_jobs:
        cat = classifier.classify(job)
        info = classifier.get_category_info(cat)
        print(f"{job['title']:<30} -> {info['emoji']} {info['name']} ({cat})")

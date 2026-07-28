"""Centralized candidate selectors for the fixture-only LinkedIn parser.

These selectors are deliberately marked as unverified. LinkedIn's robots policy
prevented a compliant live validation against the Acuity Analytics URL.
"""

JOB_CARD = "li.jobs-search-results__list-item, li.job-card-container"
JOB_ID_ATTRIBUTES = ("data-entity-urn", "data-job-id", "data-occludable-job-id")
JOB_LINK = "a.base-card__full-link, a.job-card-list__title, a[href*='/jobs/view/']"
JOB_TITLE = "h3.base-search-card__title, .job-card-list__title, [data-field='title']"
JOB_COMPANY = "h4.base-search-card__subtitle, .job-card-container__primary-description"
JOB_LOCATION = ".job-search-card__location, .job-card-container__metadata-item"
JOB_PUBLISHED_AT = "time::attr(datetime)"

DETAIL_DESCRIPTION = ".show-more-less-html__markup, .description__text, [data-field='description']"
DETAIL_CRITERIA = ".description__job-criteria-item"
DETAIL_CRITERIA_LABEL = ".description__job-criteria-subheader"
DETAIL_CRITERIA_VALUE = ".description__job-criteria-text"


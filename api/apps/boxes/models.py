import logging
import uuid
from datetime import timedelta

from django.db.models.functions import Coalesce
from humanize import naturalsize

from django.core.files.storage import FileSystemStorage
from django.urls import reverse
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone
from django.conf import settings

from apps.boxes.utils import get_file_hash


logger = logging.getLogger(__name__)

protected_storage = FileSystemStorage(
    location=settings.PROTECTED_MEDIA_ROOT,
    base_url=settings.PROTECTED_MEDIA_URL
)


class BoxQuerySet(models.QuerySet):

    def private(self):
        return self.filter(visibility=Box.PRIVATE)

    def public(self):
        return self.filter(visibility=Box.PUBLIC)

    def for_user(self, user):
        if user.is_anonymous:
            return self.filter(visibility=Box.PUBLIC)
        if user.is_staff:
            # Staff has access to all boxes
            return self

        return (self
                .filter(
                    Q(shared_with=user) |
                    Q(visibility=Box.PUBLIC) |
                    Q(owner=user))
                .distinct())

    def by_owner(self, user):
        return self.filter(owner=user)

    def annotate_pulls(self):
        return self.annotate(
            pulls=Coalesce(Sum('versions__providers__pulls'), 0)
        )


class Box(models.Model):
    PRIVATE = 'PT'
    PUBLIC = 'PC'
    VISIBILITY_CHOICES = (
        (PRIVATE, 'Private'),
        (PUBLIC, 'Public'),
    )

    objects = BoxQuerySet.as_manager()

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='boxes',
        on_delete=models.CASCADE
    )
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField('Last modified', auto_now=True)
    date_updated = models.DateTimeField(
        'Last updated',
        default=timezone.now,
        editable=False,
    )
    visibility = models.CharField(
        max_length=2, choices=VISIBILITY_CHOICES, default=PRIVATE)
    name = models.CharField(
        max_length=30,
        validators=[
            RegexValidator(
                r'^[\w.@+-]+$',
                'Enter a valid name. Name may contain '
                'letters, digits and @/./+/- only.'
            ),
        ],
    )
    short_description = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    shared_with = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through='boxes.BoxMember'
    )

    class Meta:
        unique_together = ('owner', 'name')
        verbose_name_plural = 'boxes'
        ordering = ['-date_updated']

    def __str__(self):
        return self.tag

    @property
    def tag(self):
        return '{}/{}'.format(self.owner, self.name)

    def get_perms_for_user(self, user):
        is_authenticated = user and user.is_authenticated
        is_staff = is_authenticated and user.is_staff
        is_owner = is_authenticated and self.owner == user

        if is_staff or is_owner:
            # Staff and owner have all permissions on the box
            return BoxMember.PERM_OWNER_OR_STAFF

        if is_authenticated:
            try:
                return self.boxmember_set.get(user=user).permissions
            except BoxMember.DoesNotExist:
                visibility_perms = {
                    self.PUBLIC: BoxMember.PERM_R,
                    self.PRIVATE: BoxMember.PERM_NONE,
                }
                return visibility_perms[self.visibility]

        else:
            visibility_perms = {
                self.PUBLIC: BoxMember.PERM_R,
                self.PRIVATE: BoxMember.PERM_NONE,
            }
            return visibility_perms[self.visibility]

    def user_has_perms(self, user, need_perms):
        has_perms = self.get_perms_for_user(user)
        if has_perms == BoxMember.PERM_OWNER_OR_STAFF:
            return True
        return need_perms in has_perms if need_perms else True

    def share_with(self, user, perms):
        BoxMember.objects.create(
            user=user,
            box=self,
            permissions=perms,
        )


class BoxMember(models.Model):
    PERM_R = 'R'
    PERM_RW = 'RW'
    PERM_OWNER_OR_STAFF = '*'
    PERM_NONE = ''
    PERMS_CHOICES = (
        (PERM_R, 'View/pull box'),
        (PERM_RW, 'View/pull/push box')
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE
    )
    box = models.ForeignKey('boxes.Box', on_delete=models.CASCADE)
    permissions = models.CharField(
        max_length=2,
        choices=PERMS_CHOICES,
        default=PERM_RW,
        blank=True,
    )

    class Meta:
        unique_together = ('user', 'box',)
        ordering = ['user__username']

    def user_has_perms(self, perms, user):
        return self.box.user_has_perms(perms, user)


class BoxVersion(models.Model):
    # Validate version according to Vagrant docs
    # https://www.vagrantup.com/docs/boxes/versioning.html
    VERSION_VALIDATOR = RegexValidator(
        regex=r'^(\d+)\.(\d+)(\.(\d+))?$',
        message='Invalid version number. It must be of the format '
                'X.Y.Z where X, Y, and Z are all positive integers.'
    )

    box = models.ForeignKey(
        'boxes.Box', related_name='versions', on_delete=models.CASCADE)
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField('Last modified', auto_now=True)
    date_updated = models.DateTimeField(
        'Last updated',
        default=timezone.now,
        editable=False,
    )
    version = models.CharField(
        max_length=40, validators=[VERSION_VALIDATOR])
    changes = models.TextField(blank=True)

    class Meta:
        unique_together = ('box', 'version')
        verbose_name_plural = 'box versions'
        ordering = ['-date_updated']

    def __str__(self):
        return self.tag

    @property
    def tag(self):
        return '{} v{}'.format(self.box, self.version)

    @property
    def owner(self):
        return self.box.owner

    @property
    def visibility(self):
        return self.box.visibility

    def user_has_perms(self, perms, user):
        return self.box.user_has_perms(perms, user)


def user_box_upload_path(instance, filename):
    return (
        'boxes/{owner}/{box_name}/{version}/{provider}.box'.format(
            owner=instance.version.box.owner,
            box_name=instance.version.box.name,
            version=instance.version.version,
            provider=instance.provider)
    )


class BoxProviderQuerySet(models.QuerySet):

    def empty(self):
        return self.filter(status=BoxProvider.EMPTY)

    def filled_in(self):
        return self.filter(status=BoxProvider.FILLED_IN)


class BoxProvider(models.Model):
    # Vagrant currently only supports these types
    # https://www.vagrantup.com/docs/vagrantfile/machine_settings.html
    MD5 = 'md5'
    SHA1 = 'sha1'
    SHA256 = 'sha256'
    CHECKSUM_TYPE_CHOICES = (
        (MD5, 'md5'),
        (SHA1, 'sha1'),
        (SHA256, 'sha256'),
    )

    EMPTY = 'EM'
    FILLED_IN = 'FI'
    STATUS_CHOICES = (
        (EMPTY, 'Empty'),
        (FILLED_IN, 'Filled in'),
    )

    objects = BoxProviderQuerySet.as_manager()

    version = models.ForeignKey(
        'boxes.BoxVersion', related_name='providers', on_delete=models.CASCADE)
    provider = models.CharField(max_length=100)
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField('Last modified', auto_now=True)
    date_updated = models.DateTimeField(
        'Last updated',
        default=timezone.now,
        editable=False,
    )
    file = models.FileField(
        upload_to=user_box_upload_path,
        storage=protected_storage,
        null=True,
        blank=True,
    )
    file_size = models.BigIntegerField(
        default=0,
        editable=False,
    )
    checksum_type = models.CharField(
        max_length=10,
        choices=CHECKSUM_TYPE_CHOICES,
        default=SHA256,
        editable=False,
    )
    checksum = models.CharField(
        max_length=128,
        editable=False,
    )
    pulls = models.PositiveIntegerField(
        default=0,
        editable=False,
    )
    status = models.CharField(
        max_length=2,
        choices=STATUS_CHOICES,
        default=EMPTY,
        editable=False,
    )

    class Meta:
        unique_together = ('version', 'provider')
        ordering = ['-date_updated']

    def __str__(self):
        return self.tag

    @property
    def tag(self):
        return '{} {}'.format(self.version, self.provider)

    @property
    def download_url(self):
        return reverse(
            'downloads-box',
            kwargs={
                'username': self.owner.username,
                'box_name': self.box.name,
                'version': self.version.version,
                'provider': self.provider
            }
        )

    @property
    def owner(self):
        return self.box.owner

    @property
    def box(self):
        return self.version.box

    @property
    def visibility(self):
        return self.version.box.visibility

    @property
    def human_file_size(self):
        return naturalsize(self.file_size)

    def user_has_perms(self, perms, user):
        return self.version.box.user_has_perms(perms, user)


def chunked_upload_path(instance, filename):
    return 'chunked_uploads/{user}/{filename}.part'.format(
        user=instance.box.owner,
        filename=instance.id
    )


class BoxUploadQuerySet(models.QuerySet):

    def active(self):
        expire_date = timezone.now() - timedelta(hours=settings.BOX_UPLOAD_EXPIRE_AFTER)
        return self.exclude(
            Q(status=BoxUpload.COMPLETED) |
            Q(date_created__lt=expire_date)
        )

    def not_active(self):
        expire_date = timezone.now() - timedelta(hours=settings.BOX_UPLOAD_EXPIRE_AFTER)
        return self.filter(
            Q(status=BoxUpload.COMPLETED) |
            Q(date_created__lt=expire_date)
        )


class BoxUpload(models.Model):
    STARTED = 'S'
    IN_PROGRESS = 'I'
    COMPLETED = 'C'
    STATUS_CHOICES = (
        (STARTED, 'Started'),
        (IN_PROGRESS, 'In progress'),
        (COMPLETED, 'Completed'),
    )

    objects = BoxUploadQuerySet.as_manager()

    id = models.UUIDField(unique=True, default=uuid.uuid4,
                          editable=False, primary_key=True)
    provider = models.ForeignKey(
        'BoxProvider',
        related_name='uploads',
        on_delete=models.CASCADE,
    )
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField('Last modified', auto_now=True)
    date_completed = models.DateTimeField(null=True, blank=True)
    file = models.FileField(
        max_length=255,
        upload_to=chunked_upload_path,
        storage=protected_storage,
        null=True,
        blank=True,
    )
    filename = models.CharField(max_length=255)
    file_size = models.BigIntegerField(default=0)
    offset = models.BigIntegerField(default=0)
    status = models.CharField(
        max_length=1, choices=STATUS_CHOICES, default=STARTED)
    checksum_type = models.CharField(
        max_length=10,
        choices=BoxProvider.CHECKSUM_TYPE_CHOICES)
    checksum = models.CharField(max_length=128)

    class Meta:
        ordering = ['-date_modified']
        unique_together = ('provider', 'checksum')

    def __str__(self):
        return (
            '{self.tag}: {status}'
            .format(self=self, status=self.get_status_display())
        )

    @property
    def tag(self):
        return '{} {}'.format(self.version, self.provider)

    @property
    def box(self):
        return self.version.box

    @property
    def owner(self):
        return self.box.owner

    @property
    def version(self):
        return self.provider.version

    @property
    def visibility(self):
        return self.box.visibility

    @property
    def expires(self):
        return self.date_created + timedelta(hours=settings.BOX_UPLOAD_EXPIRE_AFTER)

    @property
    def expired(self):
        return self.expires < timezone.now()

    @property
    def human_file_size(self):
        return naturalsize(self.file_size)

    @property
    def progress_size(self):
        return naturalsize(self.offset)

    @property
    def progress_percent(self):
        try:
            return self.offset / self.file_size * 100
        except ZeroDivisionError:
            return 0

    def user_has_perms(self, perms, user):
        return self.box.user_has_perms(perms, user)

    def append_chunk(self, chunk):
        assert self.status != self.COMPLETED, "Upload already completed"
        assert not self.expired, "Upload expired"

        if self.file:
            # Close file opened by Django in 'rb' mode
            self.file.file.close()
            self.file.file.open(mode='ab')
            self.file.file.write(chunk.read())
            self.file.file.close()
        else:
            self.file.save(name=chunk.name, content=chunk)

        size = self.file.size
        self.offset = size
        if size == self.file_size:
            self._complete_upload()
        else:
            self.status = self.IN_PROGRESS

        self.save()
        self.file.close()

    def _complete_upload(self):
        file_hash = get_file_hash(self.file, self.checksum_type)
        assert file_hash == self.checksum, \
            "Checksum of uploaded file ({}) doesn't match " \
            "provided checksum ({}) when upload was initiated. " \
            "Checksum type is {}."\
            .format(file_hash, self.checksum,
                    self.get_checksum_type_display())

        self._fill_in_box_provider()
        self.status = self.COMPLETED
        self.date_completed = timezone.now()

    def _fill_in_box_provider(self):
        self.provider.checksum_type = self.checksum_type
        self.provider.checksum = self.checksum
        self.provider.file_size = self.file_size
        self.provider.file.save(name=str(self.file), content=self.file)
        self.provider.status = BoxProvider.FILLED_IN
        self.provider.date_updated = timezone.now()
        self.provider.save()
        logger.info('New box uploaded: {}'.format(self.provider))

        BoxVersion.objects.filter(pk=self.provider.version_id).update(
            date_updated=self.provider.date_updated
        )
        Box.objects.filter(pk=self.provider.version.box_id).update(
            date_updated=self.provider.date_updated
        )

import logging

from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework.reverse import reverse
from rest_framework.serializers import raise_errors_on_nested_writes

from apps.boxes.models import Box
from apps.users.models import UserProfile


logger = logging.getLogger(__name__)

User = get_user_model()


class UserSerializer(serializers.HyperlinkedModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="api:v1:user-detail", lookup_field='username')
    boxes = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('url', 'username', 'email', 'password', 'is_staff', 'boxes',)
        extra_kwargs = {'password': {'write_only': True}}

    def get_boxes(self, obj):
        request = self.context['request']
        queryset = Box.objects.by_owner(obj).for_user(request.user)

        return [
            request.build_absolute_uri(
                reverse('api:v1:box-detail',
                        kwargs={
                            'username': obj.username,
                            'box_name': box.name
                        })
            )
            for box in queryset
        ]

    def create(self, validated_data):
        profile_serializer = UserProfileSerializer(
            data=validated_data['profile'])
        profile_serializer.is_valid(raise_exception=True)

        user = User(
            email=validated_data.get('email', ''),
            username=validated_data['username']
        )
        user.set_password(validated_data['password'])
        user.save()

        logger.info('New user created: {}'.format(user))

        # Profile was created on 'post_save' signal
        profile_serializer.instance = user.profile
        profile_serializer.save(user=user)
        return user

    def update(self, instance, validated_data):
        raise_errors_on_nested_writes('update', self, validated_data)

        for attr, value in validated_data.items():
            if attr == 'password':
                instance.set_password(value)
            elif attr == 'profile':
                profile_serializer = UserProfileSerializer(
                    instance=instance.profile, data=value)
                if profile_serializer.is_valid(raise_exception=True):
                    profile_serializer.save()
            else:
                setattr(instance, attr, value)

        instance.save()
        return instance


class ForStaffUserSerializer(UserSerializer):
    boxes_permissions = serializers.CharField(source='profile.boxes_permissions')

    class Meta(UserSerializer.Meta):
        fields = ('url', 'username', 'email', 'password', 'is_staff',
                  'boxes_permissions', 'boxes',)


class UserProfileSerializer(serializers.ModelSerializer):

    class Meta:
        model = UserProfile
        fields = ('boxes_permissions',)

import { kea } from 'kea'
import React from 'react'
import { toast } from 'react-toastify'
import api from 'lib/api'
import { PersonalAPIKeyType } from '~/types'
import { personalAPIKeysLogicType } from '~/types/lib/components/PersonalAPIKey/personalAPIKeysLogicType'

export const personalAPIKeysLogic = kea<personalAPIKeysLogicType<PersonalAPIKeyType>>({
    loaders: ({ values }) => ({
        keys: [
            [] as PersonalAPIKeyType[],
            {
                loadKeys: async () => {
                    const response: PersonalAPIKeyType[] = await api.get('api/personal_api_keys/')
                    return response
                },
                createKey: async (label: string) => {
                    const newKey: PersonalAPIKeyType = await api.create('api/personal_api_keys/', { label })
                    return [newKey, ...values.keys]
                },
                deleteKey: async (key: PersonalAPIKeyType) => {
                    await api.delete(`api/personal_api_keys/${key.id}/`)
                    return (values.keys as PersonalAPIKeyType[]).filter((filteredKey) => filteredKey.id != key.id)
                },
            },
        ],
    }),

    listeners: () => ({
        createKeySuccess: ({ keys }: { keys: PersonalAPIKeyType[] }) => {
            toast(<div className="text-success">Personal API key "{keys[0].label}" created.</div>)
        },
        deleteKeySuccess: ({ keys }: { keys: PersonalAPIKeyType[] }) => {
            toast(<div className="text-success">Personal API key "{keys[0].label}" deleted.</div>)
        },
    }),

    events: ({ actions }) => ({
        afterMount: [actions.loadKeys],
    }),
})
